import threading
from datetime import datetime
from typing import Callable, Dict, List


class Notifications:
    def __init__(self):
        self._items = []
        self._lock = threading.Lock()
        self._listeners: List[Callable[[], None]] = []

    def add_listener(self, cb: Callable[[], None]) -> None:
        with self._lock:
            self._listeners.append(cb)
            
    def _fire(self):
        # fire outside the lock to avoid reentrancy risk
        for cb in list(self._listeners):
            try:
                cb()
            except Exception:
                pass

    def add(self, text: str, level: str = "info"):
        item = {"ts": datetime.now().isoformat(timespec="seconds"),
                "level": level, "text": text.strip()}
        with self._lock:
            self._items.append(item)
        self._fire()

    def list(self) -> List[Dict]:
        with self._lock:
            return list(self._items)

    def clear(self):
        with self._lock:
            self._items.clear()
        self._fire()

    def count(self) -> int:
        with self._lock:
            return len(self._items)
