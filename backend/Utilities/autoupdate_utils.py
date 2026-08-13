import logging
import os
import random
import re
import shutil
import subprocess
import threading
import time
from datetime import datetime
from typing import Callable, Dict, List, Optional, Tuple


class AutoUpdater:
    """
    Tag-gated auto-updater.

    Behavior:
      - Only updates when a newer *remote* semver tag exists (e.g., v1.2.3 or V1.2.3).
      - Backs up the live settings/metadata SQLite DB before switching tags,
        restoring it if the checkout removed it (see _backup_settings).
      - Keeps the old 'git pull' path as a fallback if no tags are found.
      - Optionally restarts your service after a successful update.

    Public:
      start() -> None
      pull_now() -> Tuple[bool, str]
    """

    def __init__(
        self,
        stop_event: threading.Event,
        interval_sec: int = 1800,
        on_update_available: Optional[Callable[[int], None]] = None,
        on_updated: Optional[Callable[[str], None]] = None,
        restart_service_async: Optional[Callable[[], None]] = None,
        min_restart_interval_sec: int = 900,
        auto_restart_on_update: bool = True,
    ):
        self._stop = stop_event
        self._thread: Optional[threading.Thread] = None
        self._pull_lock = threading.Lock()
        self._interval = int(interval_sec)
        self.last_pull: Optional[Dict[str, object]] = None

        self._on_update_available = on_update_available
        self._on_updated = on_updated
        self._restart_service_async = restart_service_async
        self._auto_restart_on_update = bool(auto_restart_on_update)
        self._min_restart_interval = int(min_restart_interval_sec)
        self._last_restart_ts = 0

        from Utilities.config_events import on_settings_changed
        on_settings_changed(self._on_settings_changed)

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------
    def _on_settings_changed(self, new_data: dict) -> None:
        au = new_data.get("autoupdate", {})
        self._auto_restart_on_update = bool(au.get("enabled", True))

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._worker, daemon=True)
            self._thread.start()

    def pull_now(self) -> Tuple[bool, str]:
        """
        Manual update trigger used by the Settings dialog button.
        """
        repo = self._find_repo_root()
        if not repo:
            msg = "Repository root not found"
            self._record(False, msg)
            return False, msg

        ok, changed, out = self._update_to_newer_tag(
            repo_path=repo, timeout=180)

        # Fallback if no tags found
        if not ok and "no tags" in out.lower():
            ok, out = self._git_pull(repo_path=repo)

        # Restart if needed
        if ok and changed and self._auto_restart_on_update and self._restart_service_async:
            try:
                threading.Thread(
                    target=self._restart_service_async, daemon=True).start()
            except Exception:
                logging.exception("[AutoUpdate] restart hook failed (manual)")

        return ok, out

    # ------------------------------------------------------------------
    # Worker loop
    # ------------------------------------------------------------------
    def _worker(self) -> None:
        if self._stop.wait(timeout=random.uniform(5.0, 60.0)):
            return

        while not self._stop.is_set():
            try:
                repo = self._find_repo_root()
                if repo and shutil.which("git"):
                    env = self._git_env()

                    ok, changed, out = self._update_to_newer_tag(
                        repo_path=repo, timeout=180)
                    if self._on_updated and out:
                        try:
                            self._on_updated(out)
                        except Exception:
                            pass

                    # Fallback Logic
                    if not ok and "no tags" in out.lower():
                        upstream = self._upstream_ref(repo, env, 10)
                        if upstream:
                            ahead, behind = self._behind_counts(repo, env, 10)
                            if behind > 0 and self._on_update_available:
                                try:
                                    self._on_update_available(behind)
                                except Exception:
                                    pass
                        ok2, out2 = self._git_pull(repo, 180)
                        if self._on_updated and out2:
                            try:
                                self._on_updated(out2)
                            except Exception:
                                pass
                        changed = ok2 and self._pull_changed(out2)

                    # Restart Logic
                    if changed and self._auto_restart_on_update and self._restart_service_async:
                        now = time.time()
                        if now - self._last_restart_ts >= self._min_restart_interval:
                            self._last_restart_ts = now
                            try:
                                threading.Thread(
                                    target=self._restart_service_async, daemon=True).start()
                            except Exception:
                                logging.exception(
                                    "[AutoUpdate] restart hook failed")
                else:
                    self._record(False, "git not found or repo not detected")
            except Exception:
                logging.exception(
                    "[AutoUpdate] unexpected error during scheduled pull")

            for _ in range(self._interval):
                if self._stop.is_set():
                    return
                time.sleep(1)

    # ------------------------------------------------------------------
    # Tag-based update
    # ------------------------------------------------------------------
    def _update_to_newer_tag(self, repo_path: str, timeout: int = 180) -> Tuple[bool, bool, str]:
        env = self._git_env()
        if not shutil.which("git"):
            msg = "git not found"
            self._record(False, msg)
            return False, False, msg

        if not repo_path or not os.path.isdir(os.path.join(repo_path, ".git")):
            msg = f"Not a git repository: {repo_path}"
            self._record(False, msg)
            return False, False, msg

        upstream = self._upstream_ref(repo_path, env, timeout)
        remote = upstream[0] if upstream else "origin"
        self._fetch(repo_path, remote, env, timeout)
        self._run_git(["git", "-C", repo_path, "fetch",
                      "--tags", "--prune", remote], env, timeout)

        remote_tags = self._list_remote_semver_tags(remote, env, timeout)

        if not remote_tags:
            msg = "No tags found on remote; skipping tag-based update"
            self._record(True, msg)
            return False, False, msg

        latest_remote_tag = self._max_tag(remote_tags)
        current_tag = self._current_semver_tag(repo_path, env, timeout)
        current_ver = self._parse_semver(
            current_tag) if current_tag else (0, 0, 0)

        if self._parse_semver(latest_remote_tag) <= current_ver:
            msg = f"Already at latest tag ({current_tag or 'unknown'}); no update needed"
            self._record(True, msg)
            return True, False, msg

        # Update available
        backup_root = self._backup_settings(repo_path)
        changed, msg_checkout = self._checkout_tag(
            repo_path, latest_remote_tag, env, timeout)

        # Restore and migrate config
        self._restore_settings(repo_path, backup_root)

        msg = f"Updated to tag {latest_remote_tag}.\n{msg_checkout}"
        if backup_root:
            msg += f"\nSettings backup root: {backup_root}"

        self._record(changed, msg)
        return True, changed, msg

    def _checkout_tag(self, repo_path: str, tag: str, env, timeout: int) -> Tuple[bool, str]:
        ok_res, out_res = self._run_git(
            ["git", "-C", repo_path, "rev-list",
                "-n", "1", f"refs/tags/{tag}"],
            env,
            timeout,
        )
        if not ok_res or not out_res:
            return False, f"Failed to resolve tag {tag}: {out_res}"

        cmd = ["git", "-C", repo_path, "checkout",
               "-B", "autoupdate", f"refs/tags/{tag}"]
        ok_co, out_co = self._run_git(cmd, env, timeout)
        if not ok_co:
            return False, f"Checkout failed: {out_co}"

        return True, "Checkout succeeded"

    def _list_remote_semver_tags(self, remote: str, env, timeout: int) -> List[str]:
        ok, out = self._run_git(
            ["git", "ls-remote", "--tags", remote], env, timeout)
        if not ok or not out:
            return []
        tags: List[str] = []
        for line in out.splitlines():
            try:
                ref = line.split("\t", 1)[1]
            except Exception:
                continue
            # Dereferenced tags end in ^{}. We strip that to get the tag name.
            if ref.endswith("^{}"):
                ref = ref[:-3]
            name = ref.rsplit("/", 1)[-1]
            if self._is_semver_tag(name):
                tags.append(name)
        # Fix: Deduplicate tags to avoid assertion errors (ls-remote returns both tag and commit)
        return sorted(list(set(tags)))

    def _list_local_semver_tags(self, repo_path: str, env, timeout: int) -> List[str]:
        # RESTORED: This method was present in old code
        ok, out = self._run_git(
            ["git", "-C", repo_path, "tag", "--list"], env, timeout)
        if not ok or not out:
            return []
        return [t.strip() for t in out.splitlines() if self._is_semver_tag(t.strip())]

    def _current_semver_tag(self, repo_path: str, env, timeout: int) -> Optional[str]:
        ok, out = self._run_git(
            ["git", "-C", repo_path, "tag", "--points-at", "HEAD"], env, timeout)
        if ok and out:
            tags = [t.strip() for t in out.splitlines()
                    if self._is_semver_tag(t.strip())]
            if tags:
                return self._max_tag(tags)
        ok, out = self._run_git(
            ["git", "-C", repo_path, "describe", "--tags", "--abbrev=0"], env, timeout)
        if ok and out and self._is_semver_tag(out.strip()):
            return out.strip()
        return None

    @staticmethod
    def _is_semver_tag(tag: str) -> bool:
        return re.fullmatch(r"[Vv]?\d+\.\d+\.\d+", tag) is not None

    @staticmethod
    def _parse_semver(tag: str) -> Tuple[int, int, int]:
        m = re.fullmatch(r"[Vv]?(\d+)\.(\d+)\.(\d+)", tag or "")
        if not m:
            return (0, 0, 0)
        return (int(m.group(1)), int(m.group(2)), int(m.group(3)))

    def _max_tag(self, tags: List[str]) -> str:
        return max(tags, key=lambda t: self._parse_semver(t))

    # ------------------------------------------------------------------
    # Settings DB backup / restore
    # ------------------------------------------------------------------
    def _backup_settings(self, repo_path: str) -> Optional[str]:
        """Copy the live settings/metadata SQLite DB out of the repo before
        switching tags. SQLite is the sole live store (JSON files are
        migration-seed-only, see Utilities.config_store/database.py) --
        `git checkout` leaves an untracked database.db alone, but a device
        whose current commit predates that change could still have it
        tracked, in which case checkout would delete it. Cheap insurance."""
        from Utilities.config_store import _get_db_path
        src = _get_db_path()
        if not os.path.isfile(src):
            return None

        ts = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
        backup_root = os.path.join(repo_path, ".autoupdate_backups", ts)
        dst = os.path.join(backup_root, "database.db")

        try:
            os.makedirs(backup_root, exist_ok=True)
            shutil.copy2(src, dst)
            logging.info("[AutoUpdate] Backed up settings DB to %s", dst)
            return backup_root
        except Exception:
            logging.exception("[AutoUpdate] Failed to backup settings DB")
            return None

    def _restore_settings(self, repo_path: str, backup_root: Optional[str]) -> None:
        """Restore the settings DB only if checkout actually removed it --
        the common case is it was never touched (untracked file)."""
        if not backup_root:
            return
        from Utilities.config_store import _get_db_path
        dst = _get_db_path()
        if os.path.isfile(dst):
            return
        src = os.path.join(backup_root, "database.db")
        if not os.path.isfile(src):
            return
        try:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
            logging.info("[AutoUpdate] Restored settings DB from %s", src)
        except Exception:
            logging.exception(
                "[AutoUpdate] Failed to restore settings DB from %s", src)

    # ------------------------------------------------------------------
    # Repo detection / Git plumbing
    # ------------------------------------------------------------------
    def _find_repo_root(self) -> Optional[str]:
        if not shutil.which("git"):
            return None
        here = os.path.abspath(os.path.dirname(__file__))
        env = self._git_env()
        ok, out = self._run_git(
            ["git", "-C", here, "rev-parse", "--show-toplevel"], env, 10)
        if ok and out:
            return out.strip()
        cur = here
        while True:
            if os.path.isdir(os.path.join(cur, ".git")):
                return cur
            parent = os.path.dirname(cur)
            if parent == cur:
                break
            cur = parent
        return None

    def _git_pull(self, repo_path: Optional[str], timeout: int = 180) -> Tuple[bool, str]:
        with self._pull_lock:
            try:
                if not shutil.which("git"):
                    return False, "git not found"
                if not repo_path or not os.path.isdir(os.path.join(repo_path, ".git")):
                    return False, f"Not a git repository: {repo_path}"

                env = self._git_env()
                branch = self._current_branch(repo_path, env, timeout)
                upstream = self._upstream_ref(repo_path, env, timeout)
                if upstream:
                    remote, upstream_branch = upstream
                else:
                    remote = "origin"
                    upstream_branch = branch

                self._fetch(repo_path, remote, env, timeout)

                cmd = ["git", "-C", repo_path, "pull", "--ff-only"]
                if remote and upstream_branch:
                    cmd += [remote, upstream_branch]

                ok, out = self._run_git(cmd, env, timeout)
                if not ok and self._looks_like_dubious_ownership(out):
                    self._mark_repo_safe(repo_path, env, timeout)
                    ok, out = self._run_git(cmd, env, timeout)

                if not ok and self._looks_like_non_ff(out):
                    out += "\nHint: Non fast-forward. Manual rebase/reset may be required."

                logging.info("[AutoUpdate] git pull %s.\n%s",
                             "succeeded" if ok else "failed", out)
                self._record(ok, out)
                return ok, out
            except Exception as e:
                msg = f"git pull error: {e}"
                logging.exception("[AutoUpdate] %s", msg)
                self._record(False, msg)
                return False, msg

    def _git_env(self) -> Dict[str, str]:
        env = os.environ.copy()
        for home in (os.environ.get("HOME"), "/home/pi", "/root"):
            if home and os.path.isdir(home):
                env.setdefault("HOME", home)
                break
        env.setdefault("GIT_TERMINAL_PROMPT", "0")
        return env

    def _run_git(self, cmd, env, timeout) -> Tuple[bool, str]:
        res = subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout, env=env,
        )
        return res.returncode == 0, (res.stdout or "") + (res.stderr or "")

    def _current_branch(self, repo_path: str, env, timeout: int) -> Optional[str]:
        ok, out = self._run_git(
            ["git", "-C", repo_path, "rev-parse", "--abbrev-ref", "HEAD"], env, timeout)
        if ok:
            br = out.strip()
            return None if br == "HEAD" else br
        return None

    def _upstream_ref(self, repo_path: str, env, timeout: int) -> Optional[Tuple[str, str]]:
        ok, out = self._run_git(
            ["git", "-C", repo_path, "rev-parse", "--abbrev-ref",
                "--symbolic-full-name", "@{u}"], env, timeout,
        )
        if ok and out and "/" in out:
            r, b = out.split("/", 1)
            return r.strip(), b.strip()
        return None

    def _fetch(self, repo_path: str, remote: str, env, timeout: int) -> None:
        shallow = os.path.isfile(os.path.join(repo_path, ".git", "shallow"))
        cmd = ["git", "-C", repo_path, "fetch", "--prune", remote]
        if shallow:
            cmd += ["--depth=1", "--update-shallow"]
        self._run_git(cmd, env, timeout)

    def _mark_repo_safe(self, repo_path: str, env, timeout: int) -> None:
        self._run_git(["git", "config", "--global", "--add",
                      "safe.directory", repo_path], env, timeout)

    @staticmethod
    def _looks_like_dubious_ownership(out: str) -> bool:
        o = (out or "").lower()
        return "dubious ownership" in o or "safe.directory" in o

    @staticmethod
    def _looks_like_non_ff(out: str) -> bool:
        o = (out or "").lower()
        return "non-fast-forward" in o or "not possible to fast-forward" in o

    def _record(self, ok: bool, msg: str) -> None:
        self.last_pull = {"ok": ok, "ts": time.time(), "msg": msg}

    def _behind_counts(self, repo_path: str, env, timeout: int) -> Tuple[int, int]:
        ok, out = self._run_git(
            ["git", "-C", repo_path, "rev-list", "--left-right",
                "--count", "HEAD...@{u}"], env, timeout,
        )
        if ok and out:
            try:
                a, b = out.split()
                return int(a), int(b)
            except Exception:
                pass
        return (0, 0)

    @staticmethod
    def _pull_changed(out: str) -> bool:
        o = (out or "")
        return ("Updating" in o) or ("Fast-forward" in o) or ("Fast-Forward" in o)
