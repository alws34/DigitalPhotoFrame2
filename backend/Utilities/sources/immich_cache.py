"""Rolling streaming cache for Immich albums.

Keeps CACHE_SIZE images pre-fetched on disk. Every delay_seconds a new image
is downloaded and the oldest is evicted, so the local album dir always holds
a small sliding window of the full Immich album — no bulk download required.

Files are written atomically (temp → rename) so PhotoFrameServer never reads
a partial file.
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)

CACHE_SIZE = 5
_PREFIX = "pf_stream_"


class ImmichStreamingCache:
    def __init__(
        self,
        source,          # ImmichSource instance (already authenticated)
        remote_id: str,  # Immich album ID
        local_path: Path,
        delay_seconds: float,
        on_change: Callable[[], None] | None = None,
    ) -> None:
        self._source = source
        self._remote_id = remote_id
        self._local_path = local_path
        self._delay = max(float(delay_seconds), 1.0)
        self._on_change = on_change
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._counter = 0
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ #
    # Lifecycle                                                            #
    # ------------------------------------------------------------------ #

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="ImmichStreamingCache", daemon=True
        )
        self._thread.start()
        logger.info("[ImmichCache] Started for album %s", self._remote_id)

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=20)
        logger.info("[ImmichCache] Stopped for album %s", self._remote_id)

    # ------------------------------------------------------------------ #
    # Internal                                                             #
    # ------------------------------------------------------------------ #

    def _next_name(self, ext: str) -> str:
        with self._lock:
            n = self._counter
            self._counter += 1
        # Preview endpoint always returns JPEG regardless of original extension
        return f"{_PREFIX}{n:010d}.jpg"

    def _cached_files(self) -> list[Path]:
        """Return streaming cache files sorted oldest-first (by name = by counter)."""
        return sorted(self._local_path.glob(f"{_PREFIX}*"))

    def _download(self, asset_id: str, ext: str) -> bool:
        """Download one asset atomically. Returns True on success."""
        name = self._next_name(ext)
        dest = self._local_path / name
        tmp = self._local_path / (name + ".tmp")
        try:
            self._source.download_asset(asset_id, tmp)
            tmp.rename(dest)
            logger.debug("[ImmichCache] Cached %s", name)
            self._notify_change()
            return True
        except Exception as exc:
            logger.warning("[ImmichCache] Download failed for %s: %s", asset_id, exc)
            tmp.unlink(missing_ok=True)
            return False

    def _evict_oldest(self) -> None:
        files = self._cached_files()
        if len(files) > 1:  # always keep at least 1 so dir is never empty
            try:
                files[0].unlink()
                logger.debug("[ImmichCache] Evicted %s", files[0].name)
                self._notify_change()
            except Exception:
                pass

    def _clear_cache(self) -> None:
        changed = False
        for f in self._cached_files():
            f.unlink(missing_ok=True)
            changed = True
        for tmp in self._local_path.glob(f"{_PREFIX}*.tmp"):
            tmp.unlink(missing_ok=True)
            changed = True
        if changed:
            self._notify_change()

    def _notify_change(self) -> None:
        if self._on_change is None:
            return
        try:
            self._on_change()
        except Exception:
            logger.exception("[ImmichCache] Change callback failed")

    def _run(self) -> None:
        try:
            self._local_path.mkdir(parents=True, exist_ok=True)
            self._clear_cache()

            logger.info("[ImmichCache] Fetching asset list for album %s", self._remote_id)
            assets = self._source.list_album_assets(self._remote_id)
            if not assets:
                logger.warning("[ImmichCache] Album %s has no supported assets", self._remote_id)
                return

            n = len(assets)
            logger.info("[ImmichCache] Album has %d assets; prefetching %d", n, min(CACHE_SIZE, n))

            # Initial fill
            pos = 0
            filled = 0
            for i in range(min(CACHE_SIZE, n)):
                if self._stop.is_set():
                    return
                if self._download(*assets[i]):
                    filled += 1
            pos = min(CACHE_SIZE, n)  # next asset to download

            # Slide window: every delay_seconds evict oldest, fetch next
            while not self._stop.wait(timeout=self._delay):
                self._evict_oldest()

                if n > 0:
                    next_asset = assets[pos % n]
                    self._download(*next_asset)
                    pos += 1

        except Exception:
            logger.exception("[ImmichCache] Unhandled error in streaming cache")
