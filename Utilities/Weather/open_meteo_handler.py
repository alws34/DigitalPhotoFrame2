# Utilities/Weather/open_meteo_handler.py
import os
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import json
import logging
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Optional, Tuple

import requests
from PIL import Image  # kept because some GUIs still expect PIL.Image for legacy paths

if TYPE_CHECKING:
    from FrameServer.PhotoFrameServer import iFrame

# Local icon resolver
from Utilities.Weather.weather_icons import WeatherIconResolver


class OpenMeteoWeatherHandler:
    """
    Open-Meteo weather provider with stable local icons.
    Public API:
      - fetch_weather_data()
      - get_weather_data() -> dict with 'icon_path'
      - get_weather_icon() -> returns PIL.Image or None (legacy; loads from icon_path if needed)
      - initialize_weather_updates()  (no-op unless explicitly enabled)

    Settings (settings['open_meteo']):
      latitude, longitude  (required)
      units                "metric" | "imperial"
      temperature_unit     "celsius" | "fahrenheit"
      wind_speed_unit      "kmh" | "ms" | "mph" | "kn"
      precipitation_unit   "mm" | "inch"
      timezone             "auto" | "Europe/Berlin" | ...
      timeformat           "iso8601" | "unixtime"
      cache_ttl_minutes    int (default 60)
    """

    def __init__(self, frame: "iFrame", settings):
        self.Frame = frame
        self.weather_data = {}
        self._icon_image = None  # lazily loaded from icon_path when get_weather_icon() is called
        self.settings = settings or {}
        self.cache_file = "openmeteo_weather_cache.json"
        self.no_weather = False

        self._http = requests.Session()
        self._http.headers.update({"User-Agent": "PhotoFrame/2.1 (+weather: open-meteo)"})

        # External scheduler by default: your server runs the loop.
        self._allow_internal_scheduler = False
        self.stop_event = threading.Event()

        # Prepare icon resolver
        project_root = Path(__file__).resolve().parents[2]  # .../DigitalPhotoFrame
        self._icons = WeatherIconResolver(project_root)

        # Hourly fallback extras
        self._hourly_humidity = None
        self._hourly_wind = None

    # -------- public API --------

    def fetch_weather_data(self):
        if self.no_weather:
            return

        cfg = self._get_cfg()
        ttl = int(self._get(cfg, "cache_ttl_minutes", 60))
        now = datetime.now()

        # Cache path
        try:
            if os.path.exists(self.cache_file):
                with open(self.cache_file, "r") as f:
                    cached = json.load(f)
                ts = cached.get("timestamp")
                cached_data = cached.get("weather_data", {}) or {}
                required = {"temp", "unit", "description", "weathercode", "is_day", "icon_path"}

                fresh = False
                if ts:
                    try:
                        fresh = (datetime.fromisoformat(ts) + timedelta(minutes=ttl) > now)
                    except Exception:
                        fresh = False

                if fresh and required.issubset(set(cached_data.keys())):
                    self.weather_data = cached_data
                    self._icon_image = None  # will lazy-load if GUI calls get_weather_icon()
                    self.Frame.send_log_message("Using cached Open-Meteo weather.", logging.info)
                    return
                elif fresh:
                    self.Frame.send_log_message("Open-Meteo cache missing required fields; refetching.", logging.info)
        except Exception:
            pass

        lat, lon = self._resolve_location(cfg)
        if lat is None or lon is None:
            self.Frame.send_log_message("Open-Meteo: latitude/longitude are required in settings.", logging.error)
            self.no_weather = True
            return

        current_vars = "temperature_2m,weather_code,is_day,relative_humidity_2m,wind_speed_10m"
        hourly_vars  = "temperature_2m,weather_code,is_day,relative_humidity_2m,wind_speed_10m"

        params = {
            "latitude": lat,
            "longitude": lon,
            "timezone": self._get(cfg, "timezone", "auto"),
            "timeformat": self._get(cfg, "timeformat", "iso8601"),
            "current": current_vars,
            "current_weather": "true",
            "hourly": hourly_vars,
            "forecast_days": 1,
        }
        tu = self._get(cfg, "temperature_unit")
        if tu:
            params["temperature_unit"] = tu
        wu = self._get(cfg, "wind_speed_unit")
        if wu:
            params["wind_speed_unit"] = wu
        pu = self._get(cfg, "precipitation_unit")
        if pu:
            params["precipitation_unit"] = pu

        try:
            url = "https://api.open-meteo.com/v1/forecast"
            self.Frame.send_log_message(f"Open-Meteo request: {url} params={params}", logging.info)
            r = self._http.get(url, params=params, timeout=15)
            r.raise_for_status()
            payload = r.json() or {}

            if isinstance(payload, dict) and payload.get("error"):
                self.Frame.send_log_message(f"Open-Meteo error: {payload.get('reason')}", logging.error)
                return

            temp_val, wmo, is_day = self._extract_current(payload)
            humidity, wind = self._extract_hum_wind(payload)

            if temp_val is None or wmo is None:
                self.Frame.send_log_message("Open-Meteo: no usable current/hourly data returned.", logging.error)
                return

            temp_unit = (self._get(cfg, "temperature_unit", "celsius") or "celsius").lower()
            unit_letter = "F" if temp_unit == "fahrenheit" else "C"

            temp = round(float(temp_val))
            desc = self._wmo_description(wmo)

            # Resolve local icon file
            icon_path, _ = self._icons.resolve(int(wmo), bool(is_day), size_px=64)

            data = {
                "temp": temp,
                "unit": unit_letter,
                "description": desc,
                "weathercode": int(wmo),
                "is_day": 1 if is_day else 0,
                "icon_path": icon_path,
            }
            if humidity is not None:
                try:
                    data["humidity"] = int(round(float(humidity)))
                except Exception:
                    pass
            if wind is not None:
                try:
                    data["wind_speed"] = round(float(wind))
                except Exception:
                    data["wind_speed"] = wind
                wind_unit = (self._get(cfg, "wind_speed_unit", "kmh") or "kmh").lower()
                if wind_unit not in ("kmh", "mph", "ms", "kn"):
                    wind_unit = "kmh"
                data["wind_unit"] = wind_unit

            self.weather_data = data
            with open(self.cache_file, "w") as f:
                json.dump({"timestamp": now.isoformat(), "weather_data": data}, f)

            self._icon_image = None  # reset; lazy-load on demand
            self.Frame.send_log_message(f"Open-Meteo parsed: {self.weather_data}", logging.info)

        except requests.RequestException as e:
            self.Frame.send_log_message(f"Open-Meteo network error: {e}", logging.error)
        except Exception as e:
            self.Frame.send_log_message(f"Open-Meteo unexpected error: {e}", logging.error)

    def get_weather_data(self):
        return self.weather_data

    def get_weather_icon(self):
        """
        Legacy method for code paths that expect a PIL.Image.
        Loads from icon_path if available; returns None if not loadable.
        """
        try:
            if self._icon_image is not None:
                return self._icon_image
            p = (self.weather_data or {}).get("icon_path")
            if not p or not os.path.isfile(p):
                return None
            self._icon_image = Image.open(p)
            return self._icon_image
        except Exception:
            return None

    def initialize_weather_updates(self):
        """
        No-op by default to avoid double scheduling. Enable only if you run the handler standalone.
        """
        if not self._allow_internal_scheduler:
            return

        def _loop():
            self.fetch_weather_data()
            while not self.stop_event.is_set():
                self.stop_event.wait(3600)
                self.fetch_weather_data()

        self.stop_event = threading.Event()
        threading.Thread(target=_loop, daemon=True).start()

    # -------- internals --------

    @staticmethod
    def _get(obj, key, default=None):
        if isinstance(obj, dict):
            return obj.get(key, default)
        g = getattr(obj, "get", None)
        if callable(g):
            try:
                return g(key, default)
            except TypeError:
                try:
                    v = g(key)
                    return default if v is None else v
                except Exception:
                    return default
        try:
            return obj[key]
        except Exception:
            return default

    @staticmethod
    def _pick(mapping_like, *keys, default=None):
        for k in keys:
            if isinstance(mapping_like, dict):
                if k in mapping_like:
                    return mapping_like[k]
                continue
            g = getattr(mapping_like, "get", None)
            if callable(g):
                try:
                    sentinel = object()
                    v = g(k, sentinel)
                    if v is not sentinel:
                        return v
                except TypeError:
                    try:
                        v = g(k)
                        if v is not None:
                            return v
                    except Exception:
                        pass
            try:
                return mapping_like[k]
            except Exception:
                pass
        return default

    def _get_cfg(self):
        om = {}
        if isinstance(self.settings, dict):
            om = self.settings.get("open_meteo", {}) or {}
        else:
            om = self._get(self.settings, "open_meteo", {}) or {}

        units = (str(self._get(om, "units", "")).lower())
        if units in ("metric", "imperial"):
            om.setdefault("temperature_unit", "fahrenheit" if units == "imperial" else "celsius")
            om.setdefault("wind_speed_unit", "mph" if units == "imperial" else "kmh")
            om.setdefault("precipitation_unit", "inch" if units == "imperial" else "mm")

        om.setdefault("timezone", "auto")
        om.setdefault("timeformat", "iso8601")
        om.setdefault("temperature_unit", "celsius")
        om.setdefault("wind_speed_unit", "kmh")
        om.setdefault("precipitation_unit", "mm")
        om.setdefault("cache_ttl_minutes", 60)
        return om

    def _resolve_location(self, cfg) -> Tuple[Optional[float], Optional[float]]:
        lat = self._get(cfg, "latitude")
        lon = self._get(cfg, "longitude")
        try:
            if lat is not None and lon is not None:
                self.Frame.send_log_message(f"Open-Meteo: using coords ({lat}, {lon})", logging.info)
                return float(lat), float(lon)
        except Exception:
            pass
        return self._legacy_lat_lon()

    def _legacy_lat_lon(self) -> Tuple[Optional[float], Optional[float]]:
        w = {}
        if isinstance(self.settings, dict):
            w = self.settings.get("weather", {}) or {}
        else:
            w = self._get(self.settings, "weather", {}) or {}

        def pick(d, *keys):
            return self._pick(d, *keys, default=None)

        lat = pick(w, "lat", "latitude")
        lon = pick(w, "lon", "longitude")
        if lat is None or lon is None:
            lat = pick(self.settings, "lat", "latitude") if lat is None else lat
            lon = pick(self.settings, "lon", "longitude") if lon is None else lon

        try:
            if lat is not None and lon is not None:
                self.Frame.send_log_message(f"Open-Meteo: using legacy coords ({lat}, {lon})", logging.info)
            return (float(lat) if lat is not None else None,
                    float(lon) if lon is not None else None)
        except Exception:
            return None, None

    def _extract_current(self, payload: dict):
        temp_val, wmo, is_day = None, None, 1
        cw = payload.get("current_weather")
        if isinstance(cw, dict) and cw:
            temp_val = cw.get("temperature")
            wmo = cw.get("weathercode")
            is_day = 1 if cw.get("is_day", 1) else 0

        if temp_val is None or wmo is None:
            cur = payload.get("current", {})
            if isinstance(cur, dict) and cur:
                temp_val = cur.get("temperature_2m", temp_val)
                wmo = cur.get("weather_code", wmo)
                is_day = 1 if cur.get("is_day", is_day) else 0

        if temp_val is None or wmo is None:
            temp_val, wmo, is_day = self._from_hourly(payload)
        return temp_val, wmo, is_day

    def _extract_hum_wind(self, payload: dict):
        humidity = None
        wind = None
        cur = payload.get("current", {})
        if isinstance(cur, dict) and cur:
            humidity = cur.get("relative_humidity_2m", None)
            wind = cur.get("wind_speed_10m", None)
        if humidity is None:
            humidity = getattr(self, "_hourly_humidity", None)
        if wind is None:
            wind = getattr(self, "_hourly_wind", None)
        return humidity, wind

    def _from_hourly(self, payload: dict):
        hourly = payload.get("hourly") or {}
        times = hourly.get("time") or []
        temps = hourly.get("temperature_2m") or []
        codes = hourly.get("weather_code") or hourly.get("weathercode") or []
        is_day_arr = hourly.get("is_day") or []
        hums = hourly.get("relative_humidity_2m") or []
        winds = hourly.get("wind_speed_10m") or []

        if not times or not temps or not codes:
            return None, None, None

        try:
            offset = int(payload.get("utc_offset_seconds", 0))
            now_local = datetime.utcnow() + timedelta(seconds=offset)
            minute = now_local.minute
            if minute >= 30:
                now_local = (now_local + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
            else:
                now_local = now_local.replace(minute=0, second=0, microsecond=0)

            best_idx, best_delta = 0, None
            for i, t in enumerate(times):
                try:
                    dt = datetime.fromisoformat(t)
                except Exception:
                    continue
                delta = abs((dt - now_local.replace(tzinfo=None)).total_seconds())
                if best_delta is None or delta < best_delta:
                    best_idx, best_delta = i, delta

            temp = temps[best_idx] if best_idx < len(temps) else None
            wmo = codes[best_idx] if best_idx < len(codes) else None
            is_day = is_day_arr[best_idx] if best_idx < len(is_day_arr) else 1

            self._hourly_humidity = hums[best_idx] if best_idx < len(hums) else None
            self._hourly_wind = winds[best_idx] if best_idx < len(winds) else None

            return temp, wmo, is_day
        except Exception:
            return None, None, None

    def _wmo_description(self, code: int) -> str:
        desc = {
            0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
            45: "Fog", 48: "Depositing rime fog",
            51: "Light drizzle", 53: "Moderate drizzle", 55: "Dense drizzle",
            56: "Light freezing drizzle", 57: "Dense freezing drizzle",
            61: "Slight rain", 63: "Moderate rain", 65: "Heavy rain",
            66: "Light freezing rain", 67: "Heavy freezing rain",
            71: "Slight snow", 73: "Moderate snow", 75: "Heavy snow", 77: "Snow grains",
            80: "Rain showers", 81: "Heavy rain showers", 82: "Violent rain showers",
            85: "Snow showers", 86: "Heavy snow showers",
            95: "Thunderstorm", 96: "Thunderstorm with hail", 99: "Thunderstorm with heavy hail",
        }
        try:
            return desc.get(int(code), "Unknown")
        except Exception:
            return "Unknown"
