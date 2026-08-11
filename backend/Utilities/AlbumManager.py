"""Central coordinator for multi-source photo sync and active album selection."""
from __future__ import annotations

import logging
import re
import shutil
import threading
import time
import uuid
from pathlib import Path

from Utilities.config_events import notify_settings_changed
from Utilities.config_store import load_settings, save_settings
from Utilities.encryption import decrypt_json, encrypt_json
from Utilities.sources.base import Album, ImageSource, SyncResult
from WebAPI.database import get_db

logger = logging.getLogger(__name__)

_SOURCE_TYPES: dict[str, type[ImageSource]] = {}


def _get_source_class(source_type: str) -> type[ImageSource] | None:
    """Lazily import and cache source class by source_type string."""
    if source_type in _SOURCE_TYPES:
        return _SOURCE_TYPES[source_type]
    try:
        if source_type == "local":
            from Utilities.sources.local import LocalFolderSource

            _SOURCE_TYPES["local"] = LocalFolderSource
        elif source_type == "google_photos":
            from Utilities.sources.google_photos import GooglePhotosSource

            _SOURCE_TYPES["google_photos"] = GooglePhotosSource
        elif source_type == "immich":
            from Utilities.sources.immich import ImmichSource

            _SOURCE_TYPES["immich"] = ImmichSource
        else:
            logger.warning("[AlbumManager] Unknown source type: %s", source_type)
            return None
    except ImportError:
        logger.exception("[AlbumManager] Could not import source type: %s", source_type)
        return None
    return _SOURCE_TYPES.get(source_type)


def _sanitize_name(name: str) -> str:
    """Sanitize an album name for use as a filesystem directory name."""
    sanitized = re.sub(r"[^\w\s\-\.]", "_", name).strip()
    return sanitized or "_album"


class AlbumManager:
    """Registry of image sources, sync scheduler, and active album state."""

    def __init__(self, images_root: str | Path, encryption_key: bytes) -> None:
        self._images_root = Path(images_root).resolve()
        self._key = encryption_key

        # Sync state protected by lock
        self._lock = threading.Lock()
        self._syncing: set[str] = set()

        # Background thread control
        self._stop_event = threading.Event()
        self._sync_queue: list[str] = []  # source IDs queued for immediate sync
        self._sync_queue_event = threading.Event()
        self._thread: threading.Thread | None = None

        # Immich streaming cache (active only when an Immich album is selected)
        self._streaming_cache = None

        # Callbacks fired when active image directory changes (e.g. album switch)
        self._dir_change_callbacks: list = []

    def register_dir_change_callback(self, fn) -> None:
        """Register a callable(new_dir: str) called when the active image dir changes."""
        self._dir_change_callbacks.append(fn)

    def _notify_active_dir_changed(self, new_dir: str | Path) -> None:
        """Tell listeners that the active image directory should be reloaded."""
        for fn in self._dir_change_callbacks:
            try:
                fn(str(new_dir))
            except Exception:
                logger.exception("[AlbumManager] dir_change_callback error")

    # ------------------------------------------------------------------ #
    # Lifecycle                                                            #
    # ------------------------------------------------------------------ #

    def start(self) -> None:
        """Start the background sync thread."""
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._background_loop, name="AlbumManagerSync", daemon=True
        )
        self._thread.start()
        logger.info("[AlbumManager] Started background sync thread.")
        self._migrate_root_images_to_local()
        self._repair_album_paths()
        self._refresh_streaming_cache()
        self._refresh_immich_counts()

    def stop(self) -> None:
        """Signal background thread to stop and wait for it to finish."""
        self._stop_event.set()
        self._sync_queue_event.set()  # wake up the loop so it can exit
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=10)
        if self._streaming_cache is not None:
            self._streaming_cache.stop()
            self._streaming_cache = None
        logger.info("[AlbumManager] Background sync thread stopped.")

    def _background_loop(self) -> None:
        settings = load_settings()
        albums_cfg = settings.get("albums", {})
        sync_on_startup = bool(albums_cfg.get("sync_on_startup", True))
        interval_hours = float(albums_cfg.get("sync_interval_hours", 6))

        if sync_on_startup:
            logger.info("[AlbumManager] sync_on_startup=True; running initial sync.")
            self._safe_sync_all()

        while not self._stop_event.is_set():
            # Wait up to interval_hours for either a queued sync or the stop signal
            interval_sec = interval_hours * 3600
            deadline = time.monotonic() + interval_sec

            while not self._stop_event.is_set():
                # Process any queued immediate syncs
                with self._lock:
                    queued = list(self._sync_queue)
                    self._sync_queue.clear()

                for source_id in queued:
                    self._safe_sync_source(source_id)

                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                # Sleep until next deadline or a queued sync wakes us
                self._sync_queue_event.wait(timeout=min(remaining, 60))
                self._sync_queue_event.clear()

            if not self._stop_event.is_set():
                # Re-read settings for potentially updated interval
                try:
                    settings = load_settings()
                    albums_cfg = settings.get("albums", {})
                    interval_hours = float(albums_cfg.get("sync_interval_hours", 6))
                except Exception:
                    pass
                self._safe_sync_all()

    def _safe_sync_all(self) -> None:
        try:
            self.sync_all()
        except Exception:
            logger.exception("[AlbumManager] Unhandled error in sync_all")

    def _safe_sync_source(self, source_id: str) -> None:
        try:
            self.sync_source(source_id)
        except Exception:
            logger.exception(
                "[AlbumManager] Unhandled error syncing source %s", source_id
            )

    # ------------------------------------------------------------------ #
    # Active album                                                         #
    # ------------------------------------------------------------------ #

    def get_active_image_dir(self) -> Path:
        """Return the Path that PhotoFrameServer should play from."""
        album_id = self.get_active_album_id()
        if album_id == "all" or not album_id:
            # "All" mode plays only local images — Immich dirs are streaming-only
            local = self._images_root / "local_images"
            local.mkdir(parents=True, exist_ok=True)
            return local

        with get_db() as conn:
            row = conn.execute(
                "SELECT local_path FROM albums WHERE id = ?", (album_id,)
            ).fetchone()

        if row and row["local_path"]:
            p = Path(row["local_path"])
            if p.exists():
                return p
            logger.warning(
                "[AlbumManager] Album local_path does not exist: %s; falling back to root.",
                p,
            )
        else:
            logger.warning(
                "[AlbumManager] Album %s not found in DB; falling back to root.", album_id
            )
        return self._images_root

    def set_active_album(self, album_id: str) -> None:
        """Persist active_album_id in settings and fire config_events."""
        settings = load_settings()
        settings.setdefault("albums", {})["active_album_id"] = album_id
        save_settings(settings)
        # Fire hot-reload chain so PhotoFrameServer and MQTT react
        notify_settings_changed(settings)
        logger.info("[AlbumManager] Active album set to: %s", album_id)
        self._refresh_streaming_cache()
        self._notify_active_dir_changed(self.get_active_image_dir())

    def _refresh_streaming_cache(self) -> None:
        """Stop any running streaming cache and start a new one if active album is Immich."""
        # Stop existing cache
        if self._streaming_cache is not None:
            self._streaming_cache.stop()
            self._streaming_cache = None

        album_id = self.get_active_album_id()
        if not album_id or album_id == "all":
            return

        try:
            with get_db() as conn:
                row = conn.execute(
                    "SELECT source_id, remote_id, local_path FROM albums WHERE id = ?",
                    (album_id,),
                ).fetchone()
            if not row:
                return

            # Only stream for Immich sources
            with get_db() as conn:
                src_row = conn.execute(
                    "SELECT source_type FROM sources WHERE id = ?", (row["source_id"],)
                ).fetchone()
            if not src_row or src_row["source_type"] != "immich":
                return

            instance = self.get_source_instance(row["source_id"])
            if instance is None or not instance.is_authenticated:
                logger.warning("[AlbumManager] Immich source not authenticated; skip streaming cache")
                return

            settings = load_settings()
            delay = float(settings.get("playback", {}).get("delay_between_images", 30))

            from Utilities.sources.immich_cache import ImmichStreamingCache

            local_path = Path(row["local_path"])
            cache = ImmichStreamingCache(
                source=instance,
                remote_id=row["remote_id"],
                local_path=local_path,
                delay_seconds=delay,
                on_change=lambda: self._notify_active_dir_changed(local_path),
            )
            cache.start()
            self._streaming_cache = cache
            logger.info("[AlbumManager] Streaming cache started for album %s", album_id)

        except Exception:
            logger.exception("[AlbumManager] Failed to start streaming cache")

    def get_active_album_id(self) -> str:
        settings = load_settings()
        return settings.get("albums", {}).get("active_album_id", "all")

    # ------------------------------------------------------------------ #
    # Source management                                                    #
    # ------------------------------------------------------------------ #

    def add_source(
        self, source_type: str, name: str, config: dict, credentials: dict
    ) -> str:
        """Persist source to DB. Encrypt credentials. Return source id."""
        import json as _json

        source_id = str(uuid.uuid4())
        config_json = _json.dumps(config)
        credentials_enc = encrypt_json(credentials, self._key) if credentials else None
        now = time.time()

        with get_db() as conn:
            conn.execute(
                """
                INSERT INTO sources
                    (id, source_type, name, config_json, credentials_enc, enabled, created_at)
                VALUES (?, ?, ?, ?, ?, 1, ?)
                """,
                (source_id, source_type, name, config_json, credentials_enc, now),
            )

        logger.info("[AlbumManager] Added source %s (%s) id=%s", name, source_type, source_id)
        return source_id

    def remove_source(self, source_id: str) -> None:
        """Delete source + all its albums from DB. Delete local files."""
        # Collect local_paths before deletion
        with get_db() as conn:
            rows = conn.execute(
                "SELECT local_path FROM albums WHERE source_id = ?", (source_id,)
            ).fetchall()
            # Explicitly delete albums first (foreign_keys PRAGMA not guaranteed by get_db())
            conn.execute("DELETE FROM albums WHERE source_id = ?", (source_id,))
            conn.execute("DELETE FROM sources WHERE id = ?", (source_id,))

        for row in rows:
            if row["local_path"]:
                p = Path(row["local_path"])
                if p.exists():
                    try:
                        shutil.rmtree(p)
                        logger.info("[AlbumManager] Deleted album dir: %s", p)
                    except Exception:
                        logger.exception(
                            "[AlbumManager] Failed to delete album dir: %s", p
                        )

        logger.info("[AlbumManager] Removed source id=%s", source_id)

    def get_sources(self) -> list[dict]:
        """Return list of source dicts (no credentials)."""
        with get_db() as conn:
            rows = conn.execute(
                "SELECT id, source_type, name, enabled, last_synced_at FROM sources"
            ).fetchall()

        results = []
        for row in rows:
            src = dict(row)
            # Attempt to determine is_authenticated without exposing credentials
            src["is_authenticated"] = self._check_authenticated(row["id"])
            results.append(src)
        return results

    def _check_authenticated(self, source_id: str) -> bool:
        """Return True if stored credentials are present and look valid."""
        try:
            inst = self.get_source_instance(source_id)
            return inst is not None and inst.is_authenticated
        except Exception:
            return False

    def get_source_instance(self, source_id: str) -> ImageSource | None:
        """Load source from DB, decrypt credentials, instantiate and authenticate."""
        import json as _json

        with get_db() as conn:
            row = conn.execute(
                "SELECT source_type, config_json, credentials_enc FROM sources WHERE id = ?",
                (source_id,),
            ).fetchone()

        if not row:
            logger.warning("[AlbumManager] Source not found: %s", source_id)
            return None

        source_type = row["source_type"]
        cls = _get_source_class(source_type)
        if cls is None:
            return None

        # Merge config (plain) + credentials (encrypted) — authenticate() receives both
        config: dict = {}
        try:
            config = _json.loads(row["config_json"] or "{}")
        except Exception:
            pass

        credentials: dict = {**config}
        if row["credentials_enc"]:
            try:
                credentials.update(decrypt_json(row["credentials_enc"], self._key))
            except Exception:
                logger.exception(
                    "[AlbumManager] Failed to decrypt credentials for source %s", source_id
                )

        # Instantiate with any required constructor args based on source type.
        # LocalFolderSource needs its root dir; remote sources load config via authenticate().
        try:
            if source_type == "local":
                local_root = self._images_root / "local_images"
                instance = cls(local_images_root=local_root)
            else:
                # GooglePhotosSource and ImmichSource take no constructor args;
                # all config/credentials are applied via authenticate()
                instance = cls()
        except Exception:
            logger.exception(
                "[AlbumManager] Failed to instantiate source %s (%s)", source_id, source_type
            )
            return None

        if credentials:
            try:
                instance.authenticate(credentials)
            except Exception:
                logger.exception(
                    "[AlbumManager] authenticate() failed for source %s", source_id
                )

        return instance

    def update_source_credentials(self, source_id: str, credentials: dict) -> None:
        """Encrypt and update credentials_enc in DB."""
        credentials_enc = encrypt_json(credentials, self._key)
        with get_db() as conn:
            conn.execute(
                "UPDATE sources SET credentials_enc = ? WHERE id = ?",
                (credentials_enc, source_id),
            )
        logger.info("[AlbumManager] Updated credentials for source %s", source_id)

    # ------------------------------------------------------------------ #
    # Album management                                                     #
    # ------------------------------------------------------------------ #

    def subscribe_album(self, source_id: str, remote_id: str, name: str) -> str:
        """Insert album into DB and create local directory. Return album id."""
        album_id = f"{source_id}:{remote_id}"

        # Determine source_type to build local_path
        with get_db() as conn:
            src_row = conn.execute(
                "SELECT source_type FROM sources WHERE id = ?", (source_id,)
            ).fetchone()

        if not src_row:
            raise ValueError(f"Source not found: {source_id}")

        source_type = src_row["source_type"]
        safe_name = _sanitize_name(name)
        local_path = self._images_root / source_type / safe_name
        local_path.mkdir(parents=True, exist_ok=True)

        now = time.time()
        with get_db() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO albums
                    (id, source_id, remote_id, name, local_path, subscribed, created_at)
                VALUES (?, ?, ?, ?, ?, 1, ?)
                """,
                (album_id, source_id, remote_id, name, str(local_path), now),
            )

        logger.info(
            "[AlbumManager] Subscribed album '%s' id=%s → %s", name, album_id, local_path
        )

        # For Immich: fetch media_count immediately in background
        if source_type == "immich":
            threading.Thread(
                target=self._fetch_immich_media_count,
                args=(album_id, source_id, remote_id),
                daemon=True,
            ).start()

        return album_id

    def _fetch_immich_media_count(self, album_id: str, source_id: str, remote_id: str) -> None:
        try:
            instance = self.get_source_instance(source_id)
            if instance is None or not instance.is_authenticated:
                return
            assets = instance.list_album_assets(remote_id)
            count = len(assets)
            with get_db() as conn:
                conn.execute(
                    "UPDATE albums SET media_count = ? WHERE id = ?",
                    (count, album_id),
                )
            logger.info("[AlbumManager] Immich album %s has %d assets", album_id, count)
        except Exception:
            logger.exception("[AlbumManager] Failed to fetch media count for %s", album_id)

    def _refresh_immich_counts(self) -> None:
        """On startup, refresh media_count for all subscribed Immich albums in background."""
        try:
            with get_db() as conn:
                rows = conn.execute(
                    """SELECT a.id, a.source_id, a.remote_id
                       FROM albums a
                       JOIN sources s ON s.id = a.source_id
                       WHERE a.subscribed = 1 AND s.source_type = 'immich'"""
                ).fetchall()
        except Exception:
            return
        for row in rows:
            threading.Thread(
                target=self._fetch_immich_media_count,
                args=(row["id"], row["source_id"], row["remote_id"]),
                daemon=True,
            ).start()

    def _migrate_root_images_to_local(self) -> None:
        """Move any image files sitting directly in _images_root into local_images/.

        Uploaded photos were previously stored at Images/<file>.  After switching
        to source-segregated directories, "all" / Local Images mode reads from
        Images/local_images/.  This one-time migration moves existing root-level
        images so they continue to appear in local mode.
        """
        _IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".heic", ".heif", ".webp"}
        _SOURCE_DIRS = {"immich", "google_photos", "local", "local_images"}
        try:
            local_dir = self._images_root / "local_images"
            local_dir.mkdir(parents=True, exist_ok=True)
            moved = 0
            for f in self._images_root.iterdir():
                if f.is_file() and f.suffix.lower() in _IMAGE_EXTS:
                    dest = local_dir / f.name
                    if not dest.exists():
                        f.rename(dest)
                        moved += 1
            if moved:
                logger.info(
                    "[AlbumManager] Migrated %d image(s) from Images/ root → local_images/", moved
                )
        except Exception:
            logger.exception("[AlbumManager] _migrate_root_images_to_local failed")

    def _repair_album_paths(self) -> None:
        """Fix local_path entries that don't live under _images_root / source_type."""
        try:
            with get_db() as conn:
                rows = conn.execute(
                    """SELECT a.id, a.name, a.local_path, s.source_type
                       FROM albums a JOIN sources s ON s.id = a.source_id
                       WHERE a.subscribed = 1"""
                ).fetchall()
            for row in rows:
                expected_base = self._images_root / row["source_type"]
                current = Path(row["local_path"])
                if current.is_relative_to(expected_base):
                    continue  # already correct
                safe_name = _sanitize_name(row["name"])
                correct = expected_base / safe_name
                correct.mkdir(parents=True, exist_ok=True)
                # Move any cached files to correct location
                if current.exists():
                    import shutil as _shutil
                    for f in current.iterdir():
                        try:
                            _shutil.move(str(f), str(correct / f.name))
                        except Exception:
                            pass
                with get_db() as conn:
                    conn.execute(
                        "UPDATE albums SET local_path = ? WHERE id = ?",
                        (str(correct), row["id"]),
                    )
                logger.info(
                    "[AlbumManager] Repaired local_path for album '%s': %s → %s",
                    row["name"], current, correct,
                )
        except Exception:
            logger.exception("[AlbumManager] _repair_album_paths failed")

    def unsubscribe_album(self, album_id: str) -> None:
        """Remove album from DB and delete local files."""
        with get_db() as conn:
            row = conn.execute(
                "SELECT local_path FROM albums WHERE id = ?", (album_id,)
            ).fetchone()
            conn.execute("DELETE FROM albums WHERE id = ?", (album_id,))

        if row and row["local_path"]:
            p = Path(row["local_path"])
            if p.exists():
                try:
                    shutil.rmtree(p)
                    logger.info("[AlbumManager] Deleted album dir: %s", p)
                except Exception:
                    logger.exception(
                        "[AlbumManager] Failed to delete album dir: %s", p
                    )

        logger.info("[AlbumManager] Unsubscribed album id=%s", album_id)

    def get_albums(self) -> list[dict]:
        """Return all subscribed albums as dicts with sync_in_progress flag."""
        with get_db() as conn:
            rows = conn.execute(
                """
                SELECT id, source_id, remote_id, name, local_path,
                       cover_image, media_count, last_synced_at
                FROM albums
                WHERE subscribed = 1
                ORDER BY name
                """
            ).fetchall()

        with self._lock:
            syncing = set(self._syncing)

        results = []
        for row in rows:
            d = dict(row)
            d["sync_in_progress"] = row["id"] in syncing
            results.append(d)
        return results

    def list_remote_albums(self, source_id: str) -> list[Album]:
        """Instantiate source and call list_albums()."""
        instance = self.get_source_instance(source_id)
        if instance is None:
            raise ValueError(f"Could not instantiate source: {source_id}")
        return instance.list_albums()

    # ------------------------------------------------------------------ #
    # Sync                                                                 #
    # ------------------------------------------------------------------ #

    def sync_source(self, source_id: str) -> None:
        """Sync all subscribed albums for this source."""
        instance = self.get_source_instance(source_id)
        if instance is None:
            logger.warning(
                "[AlbumManager] Cannot sync — source not found: %s", source_id
            )
            return

        with get_db() as conn:
            albums = conn.execute(
                "SELECT id, remote_id, local_path FROM albums WHERE source_id = ? AND subscribed = 1",
                (source_id,),
            ).fetchall()

        for album_row in albums:
            album_id = album_row["id"]
            local_path = Path(album_row["local_path"])
            remote_id = album_row["remote_id"]

            with self._lock:
                self._syncing.add(album_id)

            try:
                local_path.mkdir(parents=True, exist_ok=True)
                existing_files: set[str] = {
                    f.name for f in local_path.iterdir() if f.is_file()
                }
                result: SyncResult = instance.sync_album(
                    remote_id=remote_id,
                    local_path=local_path,
                    existing_files=existing_files,
                )
                media_count = len([f for f in local_path.iterdir() if f.is_file()])
                now = time.time()

                with get_db() as conn:
                    conn.execute(
                        """
                        UPDATE albums
                        SET last_synced_at = ?, media_count = ?
                        WHERE id = ?
                        """,
                        (now, media_count, album_id),
                    )

                logger.info(
                    "[AlbumManager] Synced album %s: +%d -%d errors=%d",
                    album_id,
                    result.added,
                    result.removed,
                    len(result.errors),
                )
                if result.errors:
                    for err in result.errors:
                        logger.warning("[AlbumManager] Sync error for %s: %s", album_id, err)
            except Exception:
                logger.exception(
                    "[AlbumManager] Sync failed for album %s", album_id
                )
            finally:
                with self._lock:
                    self._syncing.discard(album_id)

        # Update source last_synced_at
        with get_db() as conn:
            conn.execute(
                "UPDATE sources SET last_synced_at = ? WHERE id = ?",
                (time.time(), source_id),
            )

    def sync_all(self) -> None:
        """Call sync_source for every enabled source."""
        with get_db() as conn:
            rows = conn.execute(
                "SELECT id FROM sources WHERE enabled = 1"
            ).fetchall()

        for row in rows:
            if self._stop_event.is_set():
                break
            self._safe_sync_source(row["id"])

    def get_sync_status(self) -> dict:
        """Return {source_id: {album_id: {in_progress, last_synced_at, media_count}}}."""
        with get_db() as conn:
            album_rows = conn.execute(
                "SELECT id, source_id, last_synced_at, media_count FROM albums WHERE subscribed = 1"
            ).fetchall()

        with self._lock:
            syncing = set(self._syncing)

        status: dict = {}
        for row in album_rows:
            source_id = row["source_id"]
            status.setdefault(source_id, {})
            status[source_id][row["id"]] = {
                "in_progress": row["id"] in syncing,
                "last_synced_at": row["last_synced_at"],
                "media_count": row["media_count"],
            }
        return status

    def trigger_sync(self, source_id: str) -> None:
        """Queue a sync for source_id to run on the background thread (non-blocking)."""
        with self._lock:
            if source_id not in self._sync_queue:
                self._sync_queue.append(source_id)
        self._sync_queue_event.set()
        logger.info("[AlbumManager] Queued sync for source %s", source_id)
