import json
from typing import Any, Dict


def load_settings(path: str) -> Dict[str, Any]:
    with open(path, "r") as f:
        settings = json.load(f)

    # -------- Basic app defaults --------
    settings.setdefault("service_name", "photoframe")
    settings.setdefault("font_name", "DejaVuSans.ttf")
    settings.setdefault("time_font_size", 48)
    settings.setdefault("date_font_size", 28)

    # Backend / API
    settings.setdefault("backend_configs", {})
    settings["backend_configs"].setdefault("host", "localhost")
    settings["backend_configs"].setdefault("server_port", 5001)

    # Screen
    scr = settings.setdefault("screen", {})
    scr.setdefault("orientation", "normal")
    scr.setdefault("brightness", 100)
    scr.setdefault("schedule_enabled", False)
    scr.setdefault("off_hour", 0)
    scr.setdefault("on_hour", 7)
    # optional multi-schedule structure used by ScreenController/SettingsForm
    scr.setdefault("schedules", [{
        "enabled": False,
        "off_hour": 0,
        "on_hour": 7,
        "days": [0, 1, 2, 3, 4, 5, 6],  # Mon..Sun (tm_wday)
    }])

    # Auto-update
    au = settings.setdefault("autoupdate", {})
    au.setdefault("enabled", True)
    au.setdefault("hour", 4)
    au.setdefault("minute", 0)
    au.setdefault("repo_path", "")
    au.setdefault("remote", "origin")
    au.setdefault("branch", None)

    # Stats overlay defaults
    st = settings.setdefault("stats", {})
    st.setdefault("font_size", 20)
    st.setdefault("font_color", "yellow")

    # Margins for overlay
    settings.setdefault("margin_left", 50)
    settings.setdefault("margin_bottom", 50)
    settings.setdefault("spacing_between", 10)
    settings.setdefault("margin_right", 50)

    # About (used in Settings/About + device name in MQTT discovery)
    about = settings.setdefault("about", {})
    about.setdefault("text", "Digital Photo Frame")
    about.setdefault("image_path", "")

    # -------- Weather provider defaults (used by weather_adapter) --------
    # If accuweather_* keys are empty, weather_adapter should fall back to Open-Meteo.
    settings.setdefault("accuweather_api_key", "")
    settings.setdefault("accuweather_location_key", "")

    om = settings.setdefault("open_meteo", {})
    om.setdefault("units", "metric")               # or "imperial"
    om.setdefault("temperature_unit", "celsius")   # or "fahrenheit"
    om.setdefault("wind_speed_unit", "kmh")        # "kmh" | "ms" | "mph" | "kn"
    om.setdefault("precipitation_unit", "mm")      # "mm" | "inch"
    om.setdefault("timezone", "auto")
    om.setdefault("timeformat", "iso8601")
    om.setdefault("cache_ttl_minutes", 60)
    # latitude/longitude are optional; if missing, your adapter should handle it.

    # -------- MQTT (for Handlers.MQTT.mqtt_bridge) --------
    mqtt = settings.setdefault("mqtt", {})
    mqtt.setdefault("enabled", False)
    mqtt.setdefault("host", "127.0.0.1")
    mqtt.setdefault("port", 1883)
    mqtt.setdefault("username", "")
    mqtt.setdefault("password", "")
    mqtt.setdefault("tls", False)
    # Leave client_id empty to let the bridge derive from hostname if desired
    mqtt.setdefault("client_id", "")
    mqtt.setdefault("base_topic", "photoframe")
    mqtt.setdefault("discovery", True)
    mqtt.setdefault("discovery_prefix", "homeassistant")
    mqtt.setdefault("interval_seconds", 10)
    mqtt.setdefault("retain_config", True)

    return settings
