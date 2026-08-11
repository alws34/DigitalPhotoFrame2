# Utilities/Weather/accuweather_handler.py
import json
import logging
import os
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import requests
from PIL import Image

if TYPE_CHECKING:
    from FrameServer.PhotoFrameServer import iFrame
from Utilities.Weather.weather_icons import WeatherIconResolver

# Basic day/night mapping for a few common AccuWeather ids; extend as needed.
# For any id not listed, we do not force a key and will fall back to remote fetch only if enabled.
ACCU_TO_KEY = {
    1: "clear-day", 2: "mainly-clear", 3: "partly-cloudy", 4: "partly-cloudy",
    5: "haze", 6: "overcast", 7: "overcast", 8: "overcast",
    11: "fog", 12: "rain-moderate", 13: "rain-showers-slight", 14: "rain-showers-moderate",
    15: "thunderstorm",
    18: "rain-moderate", 19: "snow-showers-slight", 22: "snow-moderate",
    33: "clear-night", 34: "mainly-clear", 35: "partly-cloudy",
    39: "rain-showers-moderate", 41: "thunderstorm",
}

class accuweather_handler:
    def __init__(self, frame: "iFrame", settings: dict):
        self.Frame = frame
        self.weather_data = {}
        self._icon_image = None
        self.settings = settings or {}
        self.cache_file = "weather_cache.json"
        self.no_weather = False

        project_root = Path(__file__).resolve().parents[2]
        self._icons = WeatherIconResolver(project_root)

        self.stop_event = threading.Event()
        self._allow_internal_scheduler = False  # prefer server loop

        self._http = requests.Session()
        self._http.headers.update({"User-Agent": "PhotoFrame/2.1 (+weather: accuweather)"})

        # If you truly want a remote fallback, set this True (not recommended)
        self._allow_remote_icon_fetch = False

    def fetch_weather_data(self):
        if self.no_weather:
            return
        try:
            if os.path.exists(self.cache_file):
                with open(self.cache_file, "r") as file:
                    cached = json.load(file)
                ts = cached.get("timestamp", "")
                try:
                    cache_time = datetime.fromisoformat(ts)
                except Exception:
                    cache_time = datetime.min
                if cache_time + timedelta(hours=1) > datetime.now():
                    self.weather_data = cached.get("weather_data", {}) or {}
                    self._icon_image = None
                    self.Frame.send_log_message(f"Using cached AccuWeather data: {self.weather_data}", logging.info)
                    return

            api_key = self.settings.get("accuweather_api_key", "")
            location_key = self.settings.get("accuweather_location_key", "")
            if not api_key or not location_key:
                self.Frame.send_log_message("AccuWeather API key or location key is missing.", logging.error)
                self.no_weather = True
                return

            url = f"http://dataservice.accuweather.com/currentconditions/v1/{location_key}?apikey={api_key}"
            resp = self._http.get(url, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            if not data:
                self.Frame.send_log_message("AccuWeather response empty.", logging.error)
                return
            d0 = data[0]

            temp_c = round(float(d0["Temperature"]["Metric"]["Value"]))
            desc = str(d0.get("WeatherText", ""))
            icon_id = int(d0.get("WeatherIcon", 7))
            is_day = bool(d0.get("IsDayTime", True))

            # Try local icon mapping
            icon_key = ACCU_TO_KEY.get(icon_id, None)
            icon_path = None
            if icon_key:
                # day/night tweak
                if icon_key == "clear-day" and not is_day:
                    icon_key = "clear-night"
                icon_path, _ = self._icons.resolve(0 if "clear" in icon_key else 3, is_daytime=is_day, size_px=64)
                # Note: resolve() uses WMO code, but we primarily need a file path; we passed a neutral code.

            if icon_path is None and self._allow_remote_icon_fetch:
                # Remote fallback (not recommended)
                try:
                    remote = f"https://developer.accuweather.com/sites/default/files/{icon_id:02d}-s.png"
                    self.Frame.send_log_message(f"Fetching weather icon remote: {remote}", logging.info)
                    r = self._http.get(remote, stream=True, timeout=10)
                    r.raise_for_status()
                    self._icon_image = Image.open(r.raw)
                except Exception as e:
                    self.Frame.send_log_message(f"Remote icon fetch failed: {e}", logging.warning)
                    self._icon_image = None

            unit_letter = "C"
            data_out = {
                "temp": temp_c,
                "unit": unit_letter,
                "description": desc,
                "accuweather_icon_id": icon_id,
                "is_day": 1 if is_day else 0,
            }
            if icon_path:
                data_out["icon_path"] = icon_path

            self.weather_data = data_out
            with open(self.cache_file, "w") as file:
                json.dump({"timestamp": datetime.now().isoformat(), "weather_data": self.weather_data}, file)

            self.Frame.send_log_message(f"Parsed AccuWeather data: {self.weather_data}", logging.info)

        except requests.exceptions.RequestException as e:
            self.Frame.send_log_message(f"AccuWeather network error: {e}", logging.error)
        except Exception as e:
            self.Frame.send_log_message(f"AccuWeather unexpected error: {e}", logging.error)

    def get_weather_data(self):
        return self.weather_data

    def get_weather_icon(self):
        try:
            if self._icon_image is not None:
                return self._icon_image
            p = (self.weather_data or {}).get("icon_path")
            if p and os.path.isfile(p):
                self._icon_image = Image.open(p)
                return self._icon_image
            return None
        except Exception:
            return None

    def initialize_weather_updates(self):
        if not self._allow_internal_scheduler:
            return
        def loop():
            self.fetch_weather_data()
            while not self.stop_event.is_set():
                self.stop_event.wait(3600)
                self.fetch_weather_data()
        self.stop_event = threading.Event()
        threading.Thread(target=loop, daemon=True).start()
