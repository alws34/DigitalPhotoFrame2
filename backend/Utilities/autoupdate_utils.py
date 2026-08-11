import copy
import json
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
      - Backs up all config-related JSON files before switching tags.
      - Restores user settings after update, MIGRATING them from the old flat format
        to the new nested format if necessary.
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
    # Settings backup / restore / MIGRATION
    # ------------------------------------------------------------------
    def _settings_candidates(self, repo_path: str) -> List[str]:
        # RESTORED: Legacy helper for single-file config finding
        names = ["photoframe_settings.json", "settings.json", "Settings.json"]
        return [os.path.join(repo_path, n) for n in names]

    def _find_settings_file(self, repo_path: str) -> Optional[str]:
        # RESTORED: Logic to find the active settings file
        for p in self._settings_candidates(repo_path):
            if os.path.isfile(p):
                return p
        return None

    def _list_config_files(self, repo_path: str) -> List[str]:
        results: List[str] = []
        skip_dirs = {
            ".git", ".autoupdate_backups", "__pycache__", "env", ".env", ".venv", "venv",
            ".mypy_cache", ".pytest_cache", "node_modules"
        }
        for root, dirs, files in os.walk(repo_path):
            dirs[:] = [d for d in dirs if d not in skip_dirs]
            for fn in files:
                if not fn.lower().endswith(".json"):
                    continue
                base = fn.lower()
                if "settings" not in base and "config" not in base:
                    continue
                full = os.path.join(root, fn)
                results.append(os.path.abspath(full))
        return sorted(set(results))

    def _backup_settings(self, repo_path: str) -> Optional[str]:
        cfg_files = self._list_config_files(repo_path)
        if not cfg_files:
            return None

        ts = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
        backup_root = os.path.join(repo_path, ".autoupdate_backups", ts)

        try:
            for src in cfg_files:
                rel = os.path.relpath(src, repo_path)
                dst = os.path.join(backup_root, rel)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(src, dst)
            logging.info(
                "[AutoUpdate] Backed up config files to %s", backup_root)
            return backup_root
        except Exception:
            logging.exception("[AutoUpdate] Failed to backup settings")
            return None

    def _restore_settings(self, repo_path: str, backup_path: Optional[str]) -> None:
        """
        Restore config files. If 'migration' is needed (old flat JSON -> new nested JSON),
        it maps the old user values into the new structure, preserving user data.
        """
        if not backup_path:
            return

        try:
            # Gather files to restore
            files_to_restore = []
            if os.path.isdir(backup_path):
                # New multi-file backup
                for root, _, files in os.walk(backup_path):
                    for fn in files:
                        src = os.path.join(root, fn)
                        rel = os.path.relpath(src, backup_path)
                        dst = os.path.join(repo_path, rel)
                        files_to_restore.append((src, dst))
            elif os.path.isfile(backup_path):
                # Legacy single-file backup support
                dest_file = self._find_settings_file(repo_path)
                if not dest_file:
                    dest_file = os.path.join(
                        repo_path, os.path.basename(backup_path).split(".bak-")[0])
                files_to_restore.append((backup_path, dest_file))

            for src, dst in files_to_restore:
                self._restore_and_migrate_single_file(src, dst)

            logging.info(
                "[AutoUpdate] Settings restored and migrated from %s", backup_path)

        except Exception:
            logging.exception(
                "[AutoUpdate] Failed to restore settings from %s", backup_path)

    def _restore_and_migrate_single_file(self, backup_src: str, repo_dst: str) -> None:
        """
        Reads backup (src) and target (dst).
        If both are valid JSON, merges src into dst with structure migration.
        Otherwise falls back to simple file copy.
        """
        try:
            # Ensure destination directory exists
            os.makedirs(os.path.dirname(repo_dst), exist_ok=True)

            # If destination doesn't exist, just copy the backup back.
            if not os.path.exists(repo_dst):
                shutil.copy2(backup_src, repo_dst)
                return

            # Read both
            with open(backup_src, 'r', encoding='utf-8') as f:
                user_data = json.load(f)

            with open(repo_dst, 'r', encoding='utf-8') as f:
                default_data = json.load(f)

            # Perform Migration / Merge
            merged_data = self._migrate_config_structure(
                user_data, default_data)

            # Write result back to repo_dst
            with open(repo_dst, 'w', encoding='utf-8') as f:
                json.dump(merged_data, f, indent=2)

        except (json.JSONDecodeError, UnicodeDecodeError):
            # If not valid JSON, fallback to raw copy
            logging.warning(
                f"[AutoUpdate] Non-JSON config detected, performing raw copy: {backup_src}")
            shutil.copy2(backup_src, repo_dst)
        except Exception:
            logging.exception(
                f"[AutoUpdate] Error migrating {backup_src}, falling back to raw copy.")
            shutil.copy2(backup_src, repo_dst)

    def _migrate_config_structure(self, user_data: Dict, template_data: Dict) -> Dict:
        """
        Merges user_data into template_data.
        Detects if user_data uses the old flat schema and maps it to the new nested schema.
        """
        merged = copy.deepcopy(template_data)

        # Helper to set nested dict keys
        def set_nested(d, path_list, value):
            curr = d
            for key in path_list[:-1]:
                curr = curr.setdefault(key, {})
            curr[path_list[-1]] = value

        # 1. Schema Mapping (Old -> New Path)
        mapping = {
            "font_name": ["ui", "font_name"],
            "service_name": ["system", "service_name"],
            "time_font_size": ["ui", "time_font_size"],
            "date_font_size": ["ui", "date_font_size"],
            "margin_left": ["ui", "margins", "left"],
            "margin_bottom": ["ui", "margins", "bottom"],
            "margin_right": ["ui", "margins", "right"],
            "spacing_between": ["ui", "spacing_between"],
            "shadow_blur": ["ui", "text_shadow", "blur"],
            "shadow_offset_x": ["ui", "text_shadow", "offset_x"],
            "shadow_offset_y": ["ui", "text_shadow", "offset_y"],
            "shadow_alpha": ["ui", "text_shadow", "alpha"],
            "image_quality_encoding": ["system", "image_quality_encoding"],
            "animation_duration": ["playback", "animation_duration"],
            "delay_between_images": ["playback", "delay_between_images"],
            "animation_fps": ["playback", "animation_fps"],
            "allow_translucent_background": ["effects", "allow_translucent_background"],
            "image_dir": ["system", "image_dir"],
            "date_format": ["ui", "date_format"],
            "log_file_path": ["system", "log_file_path"],
        }

        # 2. Apply Mapping
        for old_key, new_path in mapping.items():
            if old_key in user_data:
                set_nested(merged, new_path, user_data[old_key])

        # 3. Direct copy of complex top-level keys that existed in old and new
        direct_sections = [
            "open_meteo", "backend_configs", "stats", "about", "screen", "mqtt", "autoupdate"
        ]
        for sec in direct_sections:
            if sec in user_data:
                merged[sec] = user_data[sec]

        # 4. Recursive Merge for already-new structures
        for top_key in ["system", "playback", "ui", "effects"]:
            if top_key in user_data and isinstance(user_data[top_key], dict):
                self._recursive_dict_update(
                    merged.setdefault(top_key, {}), user_data[top_key])

        return merged

    def _recursive_dict_update(self, target: Dict, source: Dict) -> None:
        for k, v in source.items():
            if isinstance(v, dict) and k in target and isinstance(target[k], dict):
                self._recursive_dict_update(target[k], v)
            else:
                target[k] = v

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
