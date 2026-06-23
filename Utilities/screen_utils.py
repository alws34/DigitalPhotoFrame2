import logging
import os
import subprocess
import threading
import time
from typing import Dict, Optional, Tuple


class ScreenController:
    def __init__(self, settings: Dict, stop_event: threading.Event) -> None:
        self._settings = settings
        self._stop = stop_event
        self._state = "unknown"
        self._event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._wake_until_ts: float = 0.0

    # Public API
    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._worker, daemon=True)
            self._thread.start()

    def wake(self) -> None:
        grace_seconds = int(self._settings.get("screen", {}).get("wake_grace_seconds", 180))
        grace_seconds = max(1, int(grace_seconds))
        self._wake_until_ts = time.monotonic() + grace_seconds
        self._event.set()

    def set_brightness_percent(self, percent: int, allow_zero: bool = False) -> bool:
        dev = self._pick_default_backlight()
        if not dev:
            return False
        return self._set_brightness_percent(dev, percent, allow_zero=allow_zero)

    def is_off(self) -> bool:
        if self._state == "off":
            return True
        dev = self._pick_default_backlight()
        if not dev:
            return False
        cur, maxb = self._read_brightness(dev)
        return (cur == 0 and (maxb or 1) > 0)

    # Internal scheduling
    def _screen_cfg(self) -> Dict:
        scr = self._settings.setdefault("screen", {})
        scr.setdefault("orientation", "normal")
        scr.setdefault("brightness", 100)
        scr.setdefault("schedule_enabled", False)
        scr.setdefault("off_hour", 0)
        scr.setdefault("on_hour", 7)
        # keep the data model consistent with the UI/editor
        scr.setdefault("schedules", [{
            "enabled": False, "off_hour": 0, "on_hour": 7, "days": [0,1,2,3,4,5,6]
        }])
        # if you use the wake grace window, keep it here too
        scr.setdefault("wake_grace_seconds", 180)
        return scr

    def _worker(self) -> None:
        ev = self._event

        while not self._stop.is_set():
            try:
                scr = self._screen_cfg()
                enabled = bool(scr.get("schedule_enabled", False))
                off_h = int(scr.get("off_hour", 0)) % 24
                on_h  = int(scr.get("on_hour", 7)) % 24
                now_h = self._hour_now()

                # Desired state from schedule
                desired = "off" if (enabled and self._in_off_period(now_h, off_h, on_h)) else "on"

                # override with wake grace window
                if desired == "off" and time.monotonic() < float(self._wake_until_ts or 0):
                    desired = "on"

                dev = self._pick_default_backlight()
                if not dev:
                    if ev.wait(timeout=30.0):
                        ev.clear()
                    continue

                if desired == "off" and self._state != "off":
                    try:
                        from Utilities.brightness import set_screen_power  # noqa: I001, PLC0415
                        set_screen_power(False)
                    except Exception:
                        self._set_brightness_percent(dev, 0, allow_zero=True)
                    self._state = "off"

                elif desired == "on" and self._state != "on":
                    restore = int(scr.get("brightness", 100))
                    restore = max(10, min(100, restore))
                    try:
                        from Utilities.brightness import set_screen_power  # noqa: I001, PLC0415
                        set_screen_power(True)
                    except Exception:
                        pass
                    self._set_brightness_percent(dev, restore, allow_zero=False)
                    self._state = "on"

            except Exception:
                logging.exception("screen worker tick failed")

            # Wait until next tick or an explicit wake()
            if ev.wait(timeout=30.0):
                ev.clear()
                # On an explicit wake, ensure we immediately move to "on" in case we were off.
                # The grace check above will cause 'desired' to be "on" on the next loop.
                # No extra work is strictly needed here.
                pass

    # Helpers
    @staticmethod
    def _hour_now() -> int:
        try:
            return int(time.strftime("%H"))
        except Exception:
            return 0

    @staticmethod
    def _in_off_period(now_h: int, off_h: int, on_h: int) -> bool:
        now_h %= 24
        off_h %= 24
        on_h %= 24
        if off_h == on_h:
            return False
        if off_h < on_h:
            return off_h <= now_h < on_h
        return now_h >= off_h or now_h < on_h

    @staticmethod
    def _list_backlights() -> list:
        base = "/sys/class/backlight"
        if not os.path.isdir(base):
            return []
        devs = [d for d in os.listdir(base) if os.path.isdir(os.path.join(base, d))]
        devs.sort(key=lambda n: (0 if n.startswith("rpi") or "rpi_backlight" in n or "raspberry" in n else 1, n))
        return devs

    def _pick_default_backlight(self) -> Optional[str]:
        bls = self._list_backlights()
        return bls[0] if bls else None

    @staticmethod
    def _read_brightness(dev: str) -> Tuple[Optional[int], Optional[int]]:
        base = os.path.join("/sys/class/backlight", dev)
        try:
            with open(os.path.join(base, "max_brightness"), "r") as f:
                maxb = int(f.read().strip())
            with open(os.path.join(base, "brightness"), "r") as f:
                cur = int(f.read().strip())
            return cur, maxb
        except Exception:
            return None, None

    @staticmethod
    def _write_brightness_value(dev: str, value: int) -> bool:
        base = os.path.join("/sys/class/backlight", dev)
        path = os.path.join(base, "brightness")
        try:
            with open(path, "w") as f:
                f.write(str(value))
            return True
        except PermissionError:
            cmd = f"echo {value} | sudo tee {path}"
            try:
                subprocess.run(cmd, shell=True, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return True
            except subprocess.CalledProcessError:
                return False
        except Exception:
            return False

    def _set_brightness_percent(self, dev: str, percent: int, allow_zero: bool = False) -> bool:
        percent = int(percent)
        percent = max(0 if allow_zero else 10, min(100, percent))
        cur, maxb = self._read_brightness(dev)
        if maxb in (None, 0):
            return False
        value = 0 if percent == 0 else int(round(percent * maxb / 100.0))
        value = min(maxb, value)
        return self._write_brightness_value(dev, value)
