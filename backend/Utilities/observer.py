# Utilities/observer.py
import logging
import os
import threading
import time
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

if TYPE_CHECKING:
    from FrameServer.PhotoFrameServer import iFrame


class iBoserver(ABC):
    @abstractmethod
    def start_observer(self): ...
    @abstractmethod
    def stop_observer(self): ...
    @abstractmethod
    def reload_images(self) -> int: ...

class ImageChangeHandler(FileSystemEventHandler):
    def __init__(self, observer: "ImagesObserver", images_dir: str):
        self.observer = observer
        self.images_dir = images_dir

    # Any create/delete/move triggers the coalescing event.
    def on_any_event(self, event):
        self.observer._notify_fs_event()

class ImagesObserver(iBoserver):
    def __init__(self, frame: "iFrame", images_dir: str = "Images"):
        self.frame = frame
        self.images_dir = images_dir
        self._observer = None
        self._started = False

        # Debounce/coalescing state (no Timer objects)
        self._fs_event = threading.Event()
        self._stop_event = threading.Event()
        self._debounce_quiet_seconds = 1.0  # reload once after 1s of quiet
        self._debounce_thread = None

        # Cache of current set of files
        self.images = []

    def start_observer(self):
        if self._started:
            self.frame.send_log_message("Directory observer already started; skipping.", logging.DEBUG)
            return

        # Initial load
        self.reload_images()

        # Start watchdog observer
        handler = ImageChangeHandler(self, images_dir=self.images_dir)
        self._observer = Observer()
        self._observer.schedule(handler, self.images_dir, recursive=True)
        self._observer.start()

        # Start single debounce worker
        self._stop_event.clear()
        self._debounce_thread = threading.Thread(
            target=self._debounce_worker, name="ImagesObserverDebounce", daemon=True
        )
        self._debounce_thread.start()

        self._started = True
        self.frame.send_log_message("Directory observer started.", logging.INFO)

    def stop_observer(self):
        if not self._started:
            return
        self.frame.send_log_message("Stopping directory observer...", logging.INFO)
        try:
            self._stop_event.set()
            if self._debounce_thread:
                self._fs_event.set()  # wake it
                self._debounce_thread.join(timeout=2.0)
        except Exception:
            pass
        try:
            self._observer.stop()
            self._observer.join(timeout=2.0)
        except Exception:
            pass
        self._started = False
        self.frame.send_log_message("Directory observer stopped.", logging.INFO)

    # Called by the watchdog handler
    def _notify_fs_event(self):
        self._fs_event.set()

    def _debounce_worker(self):
        """
        Wait for any FS event, then wait for a quiet period, then reload once.
        Single thread for the entire lifetime; no Timer objects => no thread ident warnings.
        """
        while not self._stop_event.is_set():
            # Wait until something happens or we are asked to stop
            if not self._fs_event.wait(timeout=0.5):
                continue
            # We got an event; now wait for quiet period
            while not self._stop_event.is_set():
                self._fs_event.clear()
                # Quiet window
                time.sleep(self._debounce_quiet_seconds)
                # If new events arrived during quiet window, loop and wait again
                if self._fs_event.is_set():
                    continue
                break
            if self._stop_event.is_set():
                break
            # Quiet window achieved: reload once
            try:
                self.reload_images()
            except Exception:
                self.frame.send_log_message("ImagesObserver: reload failed", logging.ERROR)

    def reload_images(self) -> int:
        new_list = self.get_images_from_directory()
        # Compare by set to ignore ordering-only differences
        if set(new_list) != set(self.images):
            self.images = new_list
            self.frame.send_log_message(f"Images changed. Now tracking {len(self.images)} images.", logging.INFO)
            # Ask the frame to refresh its own list/shuffle, but do not restart transitions
            try:
                if hasattr(self.frame, "update_images_list"):
                    self.frame.update_images_list()
            except Exception:
                pass
        return len(self.images)

    def get_images_from_directory(self) -> list:
        valid_extensions = (".jpg", ".jpeg", ".png", ".gif", ".bmp", ".heic", ".heif")
        images = []
        for root, _, files in os.walk(self.images_dir):
            for file in files:
                if file.lower().endswith(valid_extensions):
                    images.append(os.path.join(root, file))
        return images
