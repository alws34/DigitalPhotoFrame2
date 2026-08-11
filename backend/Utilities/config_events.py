"""In-process pub/sub for settings changes + watchdog sentinel watcher."""
from __future__ import annotations

import os
import threading
from typing import Callable

_callbacks: list[Callable[[dict], None]] = []
_lock = threading.Lock()
_observer = None


def on_settings_changed(callback: Callable[[dict], None]) -> None:
    """Register a callback to be called with fresh settings dict on every change."""
    with _lock:
        _callbacks.append(callback)


def notify_settings_changed(new_data: dict) -> None:
    """Fire all registered callbacks. Swallows individual exceptions."""
    with _lock:
        cbs = list(_callbacks)
    for cb in cbs:
        try:
            cb(new_data)
        except Exception as e:
            print(f"[Config] Callback {cb} raised: {e}")


def start_watcher() -> None:
    """Start watchdog observer on the sentinel file directory."""
    global _observer
    if _observer is not None:
        stop_watcher()
    from watchdog.events import FileSystemEventHandler
    from watchdog.observers import Observer

    sentinel_path = os.environ.get("PF_SENTINEL_PATH", "/tmp/pf_settings.sentinel")
    sentinel_dir = os.path.dirname(os.path.abspath(sentinel_path))

    class _Handler(FileSystemEventHandler):
        def on_modified(self, event):
            if os.path.abspath(event.src_path) == os.path.abspath(sentinel_path):
                _reload_and_notify()

        def on_created(self, event):
            if os.path.abspath(event.src_path) == os.path.abspath(sentinel_path):
                _reload_and_notify()

    _observer = Observer()
    _observer.schedule(_Handler(), sentinel_dir, recursive=False)
    _observer.daemon = True
    _observer.start()
    print(f"[Config] Watching {sentinel_path} for settings changes")


def stop_watcher() -> None:
    global _observer
    if _observer:
        _observer.stop()
        _observer.join()
        _observer = None


def _reload_and_notify() -> None:
    try:
        from Utilities.config_store import load_settings
        data = load_settings()
        notify_settings_changed(data)
        print("[Config] Settings hot-reloaded")
    except Exception as e:
        print(f"[Config] Hot reload failed: {e}")
