"""Single source of truth for reading and writing application settings."""
from __future__ import annotations

import json
import os
import time
import zoneinfo
from typing import Any


def apply_system_timezone(settings: dict) -> None:
    """Apply system.timezone from settings to the process environment."""
    tz = settings.get("system", {}).get("timezone", "")
    if tz and tz != "System Default":
        os.environ["TZ"] = tz
    else:
        os.environ.pop("TZ", None)
    time.tzset()


def _get_db_path() -> str:
    return os.environ.get(
        "PF_DB_PATH",
        os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "WebAPI", "database.db",
        ),
    )


def _get_sentinel_path() -> str:
    return os.environ.get("PF_SENTINEL_PATH", "/tmp/pf_settings.sentinel")


def _get_db():
    import sqlite3
    conn = sqlite3.connect(_get_db_path())
    conn.row_factory = sqlite3.Row
    return conn


def get_default_settings() -> dict[str, Any]:
    return {
        "about": {
            "image_path": "",
            "text": "Digital Photo Frame",
            "version": "1.0.2",
        },
        "autoupdate": {
            "branch": "None",
            "enabled": True,
            "hour": 4,
            "minute": 0,
            "remote": "origin",
            "repo_path": "",
            "shallow_ok": True,
        },
        "backend_configs": {
            "host": "0.0.0.0",
            "idle_fps": 5,
            "server_port": 5002,
            "stream_height": 1080,
            "stream_width": 1920,
            "supersecretkey": "CHANGE_ME_IN_PRODUCTION",
        },
        "effects": {
            "allow_translucent_background": True,
            "background_type": "blur",
            "background_color": "#000000",
            "preset": "custom",
            "tint_color": "#ffffff",
            "tint_opacity": 0.0,
            "background_blur_enabled": True,
            "background_blur_radius": 61,
            "background_opacity": 0.4,
            "shadow_enabled": True,
            "shadow_blur_radius": 71,
            "shadow_opacity": 0.85,
        },
        "mqtt": {
            "base_topic": "photoframe",
            "client_id": "photoframe-livingroom",
            "discovery": True,
            "discovery_prefix": "homeassistant",
            "enabled": False,
            "host": "",
            "interval_seconds": 1,
            "password": "",
            "port": 1883,
            "retain_config": True,
            "tls": False,
            "username": "",
        },
        "open_meteo": {
            "cache_ttl_minutes": 60,
            "latitude": "32.0853",
            "longitude": "34.7818",
            "precipitation_unit": "mm",
            "temperature_unit": "celsius",
            "timeformat": "iso8601",
            "timezone": "auto",
            "units": "metric",
            "wind_speed_unit": "kmh",
        },
        "albums": {
            "active_album_id": "all",
            "sync_interval_hours": 6,
            "sync_on_startup": True,
            "sync_delete_removed": True,
        },
        "playback": {
            "animation_duration": 10,
            "animation_fps": 30,
            "delay_between_images": 30,
        },
        "screen": {
            "brightness": 100,
            "off_hour": 0,
            "on_hour": 8,
            "orientation": 270,
            "schedule_enabled": False,
            "schedules": [
                {"days": [0, 1, 2, 3, 4, 5, 6], "enabled": False, "off_hour": 0, "on_hour": 10}
            ],
        },
        "stream": {
            "show_overlay": False,
        },
        "stats": {"font_color": "yellow", "font_size": 20, "show": False, "corner": "top-left", "margin_x": 20, "margin_y": 20},
        "system": {
            "image_dir": "Images",
            "image_quality_encoding": 100,
            "log_file_path": "./FrameServer/PhotoFrame.log",
            "sidebar_collapsed": False,
            "timezone": "System Default",
        },
        "ui": {
            "contrast_text": True,
            "date_font_size": 60,
            "date_format": "ddd/MM/yyyy",
            "font_name": "arial.ttf",
            "is_24h": False,
            "margins": {"bottom": 30, "left": 80, "right": 50},
            "show_weather": True,
            "spacing_between": 50,
            "text_shadow": {"alpha": 230, "blur": 16, "offset_x": 2, "offset_y": 2},
            "time_font_size": 80,
            "datetime_corner": "bottom-left",
            "weather_corner": "bottom-right",
        },
    }


SETTINGS_SCHEMA: dict = {
    "albums": {
        "active_album_id":      {"type": "str",  "label": "Active Album",             "restart_required": False, "ui": "album_select"},
        "sync_interval_hours":  {"type": "int",  "label": "Sync Interval (hours)",    "min": 0, "max": 72, "step": 1, "restart_required": False, "ui": "clock"},
        "sync_on_startup":      {"type": "bool", "label": "Sync on Startup",          "restart_required": False},
        "sync_delete_removed":  {"type": "bool", "label": "Delete Removed Media",     "restart_required": False},
    },
    "autoupdate": {
        "branch":     {"type": "str",  "label": "Branch",               "restart_required": False},
        "enabled":    {"type": "bool", "label": "Enable Auto-Update",    "restart_required": False},
        "hour":       {"type": "int",  "label": "Update Hour",   "min": 0,  "max": 23,  "step": 1, "restart_required": False, "ui": "clock"},
        "minute":     {"type": "int",  "label": "Update Minute", "min": 0,  "max": 59,  "step": 1, "restart_required": False, "ui": "clock"},
        "remote":     {"type": "str",  "label": "Git Remote",            "restart_required": False},
        "repo_path":  {"type": "str",  "label": "Repo Path",             "restart_required": False},
        "shallow_ok": {"type": "bool", "label": "Allow Shallow Clone",   "restart_required": False},
    },
    "backend_configs": {
        "server_port":    {"type": "int",      "label": "Server Port",    "min": 1,   "max": 65535, "step": 1,  "restart_required": True,  "no_slider": True},
        "host":           {"type": "str",      "label": "Bind Host",      "restart_required": True},
        "supersecretkey": {"type": "password", "label": "Secret Key",     "restart_required": True},
        "stream_height":  {"type": "int",      "label": "Stream Height",  "min": 100, "max": 4320,  "step": 10, "restart_required": True,  "no_slider": True},
        "stream_width":   {"type": "int",      "label": "Stream Width",   "min": 100, "max": 7680,  "step": 10, "restart_required": True,  "no_slider": True},
        "idle_fps":       {"type": "int",      "label": "Idle FPS",       "min": 1,   "max": 30,    "step": 1,  "restart_required": True,  "no_slider": True},
    },
    "effects": {
        "allow_translucent_background": {"type": "bool",  "label": "Enable Translucent Background", "restart_required": False},
        "background_type":   {"type": "enum",  "label": "Background Type",    "choices": ["blur", "color", "none"], "restart_required": False, "ui": "background_type_picker"},
        "background_color":  {"type": "str",   "label": "Background Color",   "restart_required": False, "ui": "hex_color"},
        "preset":            {"type": "enum",  "label": "Preset",             "choices": ["custom", "milk_glass", "tinted_glass", "frosted_dark", "clear"], "restart_required": False, "ui": "preset_picker"},
        "tint_color":        {"type": "str",   "label": "Tint Color",         "restart_required": False, "ui": "hex_color"},
        "tint_opacity":      {"type": "float", "label": "Tint Opacity",       "min": 0.0, "max": 1.0, "step": 0.05, "restart_required": False, "no_slider": True},
        "background_blur_enabled":      {"type": "bool",  "label": "Enable Background Blur",         "restart_required": False},
        "background_blur_radius":       {"type": "int",   "label": "Background Blur",  "min": 0, "max": 200, "step": 2,    "restart_required": False, "no_slider": True},
        "background_opacity":           {"type": "float", "label": "Background Opacity","min": 0.0,"max": 1.0,"step": 0.05, "restart_required": False, "no_slider": True},
        "shadow_enabled":               {"type": "bool",  "label": "Enable Shadow",                  "restart_required": False},
        "shadow_blur_radius":           {"type": "int",   "label": "Shadow Blur",      "min": 0, "max": 200, "step": 2,    "restart_required": False, "no_slider": True},
        "shadow_opacity":               {"type": "float", "label": "Shadow Opacity",   "min": 0.0,"max": 1.0,"step": 0.05, "restart_required": False, "no_slider": True},
    },
    "mqtt": {
        "base_topic":       {"type": "str",      "label": "Base Topic",           "restart_required": False},
        "client_id":        {"type": "str",      "label": "Client ID",            "restart_required": False},
        "discovery":        {"type": "bool",     "label": "HA Discovery",         "restart_required": False},
        "discovery_prefix": {"type": "str",      "label": "Discovery Prefix",     "restart_required": False},
        "enabled":          {"type": "bool",     "label": "Enable MQTT",          "restart_required": False},
        "host":             {"type": "str",      "label": "Broker Host",          "restart_required": False},
        "interval_seconds": {"type": "int",      "label": "Publish Interval (s)", "min": 1, "max": 3600, "step": 1,  "restart_required": False, "no_slider": True},
        "password":         {"type": "password", "label": "Password",             "restart_required": False},
        "port":             {"type": "int",      "label": "Broker Port",          "min": 1, "max": 65535, "step": 1, "restart_required": False, "no_slider": True},
        "retain_config":    {"type": "bool",     "label": "Retain Config",        "restart_required": False},
        "tls":              {"type": "bool",     "label": "Use TLS",              "restart_required": False},
        "username":         {"type": "str",      "label": "Username",             "restart_required": False},
    },
    "open_meteo": {
        "cache_ttl_minutes":  {"type": "int",            "label": "Cache TTL (min)",    "min": 1, "max": 1440, "step": 5, "restart_required": False, "no_slider": True},
        "latitude":           {"type": "numeric_string", "label": "Latitude",           "restart_required": False},
        "longitude":          {"type": "numeric_string", "label": "Longitude",          "restart_required": False},
        "precipitation_unit": {"type": "enum",           "label": "Precipitation Unit", "choices": ["mm", "inch"],               "restart_required": False},
        "temperature_unit":   {"type": "enum",           "label": "Temperature Unit",   "choices": ["celsius", "fahrenheit"],    "restart_required": False},
        "timeformat":         {"type": "str",            "label": "Time Format",        "restart_required": False},
        "timezone":           {"type": "str",            "label": "Timezone",           "restart_required": False},
        "units":              {"type": "enum",           "label": "Units",              "choices": ["metric", "imperial"],       "restart_required": False},
        "wind_speed_unit":    {"type": "enum",           "label": "Wind Speed Unit",    "choices": ["kmh", "mph", "ms", "kn"],  "restart_required": False},
    },
    "playback": {
        "animation_duration":   {"type": "int", "label": "Animation Duration (s)",   "min": 1, "max": 120, "step": 1, "restart_required": False, "no_slider": True},
        "animation_fps":        {"type": "int", "label": "Animation FPS",            "min": 1, "max": 60,  "step": 1, "restart_required": False, "no_slider": True},
        "delay_between_images": {"type": "int", "label": "Delay Between Images (s)", "min": 1, "max": 600, "step": 5, "restart_required": False, "no_slider": True},
    },
    "screen": {
        "brightness":       {"type": "int",  "label": "Brightness (%)",  "min": 0, "max": 100, "step": 5,  "restart_required": False},
        "off_hour":         {"type": "int",  "label": "Screen Off Hour", "min": 0, "max": 23,  "step": 1,  "restart_required": False, "ui": "clock"},
        "on_hour":          {"type": "int",  "label": "Screen On Hour",  "min": 0, "max": 23,  "step": 1,  "restart_required": False, "ui": "clock"},
        "orientation":      {"type": "int",  "label": "Orientation (°)", "choices": [0, 90, 180, 270],       "restart_required": False, "ui": "orientation_buttons"},
        "schedule_enabled": {"type": "bool", "label": "Enable Schedule",                                     "restart_required": False},
    },
    "stream": {
        "show_overlay": {"type": "bool", "label": "Show Overlay on Stream", "restart_required": False},
    },
    "stats": {
        "font_color": {"type": "color", "label": "Font Color", "choices": ["yellow", "white", "red", "green", "blue"], "restart_required": False},
        "font_size":  {"type": "int",   "label": "Font Size",  "min": 8, "max": 100, "step": 2, "restart_required": False, "no_slider": True},
        "show":       {"type": "bool",  "label": "Show Stats",                                   "restart_required": False},
        "corner": {
            "type": "enum",
            "label": "Stats Corner",
            "choices": ["top-left", "top-right", "bottom-left", "bottom-right"],
            "restart_required": False,
            "ui": "corner_picker",
        },
        "margin_x": {"type": "int", "label": "Stats Margin X (px)", "min": 0, "max": 200, "step": 1, "restart_required": False, "no_slider": True},
        "margin_y": {"type": "int", "label": "Stats Margin Y (px)", "min": 0, "max": 200, "step": 1, "restart_required": False, "no_slider": True},
    },
    "system": {
        "image_dir":              {"type": "str",  "label": "Image Directory",    "restart_required": False},
        "image_quality_encoding": {"type": "int",  "label": "Image Quality (%)", "min": 1, "max": 100, "step": 5, "restart_required": False, "no_slider": True},
        "log_file_path":          {"type": "str",  "label": "Log File Path",     "restart_required": False},
        "sidebar_collapsed":      {"type": "bool", "label": "Sidebar Collapsed", "restart_required": False},
        "timezone": {
            "type":             "enum",
            "label":            "Timezone",
            "choices":          ["System Default"] + sorted(zoneinfo.available_timezones() or []),
            "restart_required": False,
            "ui":               "timezone_select",
        },
    },
    "ui": {
        "contrast_text":   {"type": "bool", "label": "Contrast Text",  "restart_required": False},
        "date_font_size":  {"type": "int",  "label": "Date Font Size", "min": 10, "max": 200, "step": 5, "restart_required": False, "no_slider": True},
        "date_format":     {"type": "str",  "label": "Date Format",    "restart_required": False},
        "font_name":       {"type": "str",  "label": "Font File Name", "restart_required": False},
        "is_24h":          {"type": "bool", "label": "24-Hour Clock",  "restart_required": False},
        "margins": {
            "bottom": {"type": "int", "label": "Bottom Margin", "min": 0, "max": 300, "step": 5, "restart_required": False, "no_slider": True},
            "left":   {"type": "int", "label": "Left Margin",   "min": 0, "max": 300, "step": 5, "restart_required": False, "no_slider": True},
            "right":  {"type": "int", "label": "Right Margin",  "min": 0, "max": 300, "step": 5, "restart_required": False, "no_slider": True},
        },
        "show_weather":    {"type": "bool", "label": "Show Weather",    "restart_required": False},
        "spacing_between": {"type": "int",  "label": "Spacing Between", "min": 0, "max": 300, "step": 5, "restart_required": False, "no_slider": True},
        "text_shadow": {
            "alpha":    {"type": "int", "label": "Shadow Alpha",    "min": 0,   "max": 255, "step": 5,  "restart_required": False, "no_slider": True},
            "blur":     {"type": "int", "label": "Shadow Blur",     "min": 0,   "max": 50,  "step": 1,  "restart_required": False, "no_slider": True},
            "offset_x": {"type": "int", "label": "Shadow Offset X", "min": -50, "max": 50,  "step": 1, "restart_required": False, "no_slider": True},
            "offset_y": {"type": "int", "label": "Shadow Offset Y", "min": -50, "max": 50,  "step": 1, "restart_required": False, "no_slider": True},
        },
        "time_font_size": {"type": "int", "label": "Time Font Size", "min": 10, "max": 300, "step": 5, "restart_required": False, "no_slider": True},
        "datetime_corner": {
            "type": "enum",
            "label": "Date/Time Corner",
            "choices": ["bottom-left", "bottom-right", "top-left", "top-right"],
            "restart_required": False,
            "ui": "corner_picker",
        },
        "weather_corner": {
            "type": "enum",
            "label": "Weather Corner",
            "choices": ["bottom-left", "bottom-right", "top-left", "top-right"],
            "restart_required": False,
            "ui": "corner_picker",
        },
    },
    "admin_ui": {
        "accent_color":     {"type": "enum", "label": "Accent Color",     "choices": ["indigo", "sky", "emerald", "rose"], "restart_required": False, "ui": "color_theme"},
        "motion_intensity": {"type": "enum", "label": "Motion Intensity", "choices": ["subtle", "cinematic"],              "restart_required": False, "ui": "motion_select"},
    },
}


def get_field_schema(path: str) -> "dict | None":
    """Resolve a dotted path like 'ui.margins.left' to its leaf descriptor, or None."""
    parts = path.split(".")
    node = SETTINGS_SCHEMA
    for p in parts:
        if not isinstance(node, dict) or p not in node:
            return None
        node = node[p]
    if isinstance(node, dict) and "type" in node:
        return node
    return None


def get_restart_required_paths() -> frozenset:
    """Return frozenset of every dotted path whose schema has restart_required=True."""
    result: set = set()

    def _walk(node: dict, prefix: str) -> None:
        for k, v in node.items():
            path = f"{prefix}.{k}" if prefix else k
            if isinstance(v, dict):
                if "type" in v:
                    if v.get("restart_required"):
                        result.add(path)
                else:
                    _walk(v, path)

    _walk(SETTINGS_SCHEMA, "")
    return frozenset(result)


def _deep_merge(base: dict, override: dict) -> dict:
    result = dict(base)
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


def load_settings(json_path: str | None = None) -> dict[str, Any]:
    """Load settings from DB. On first run, migrates from json_path if DB is empty."""
    conn = _get_db()
    try:
        # Ensure table exists
        conn.execute("""
            CREATE TABLE IF NOT EXISTS app_settings (
                key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at REAL
            )
        """)
        conn.commit()
        row = conn.execute(
            "SELECT value FROM app_settings WHERE key = 'main'"
        ).fetchone()
    finally:
        conn.close()

    if row:
        try:
            saved = json.loads(row["value"])
            return _deep_merge(get_default_settings(), saved)
        except (json.JSONDecodeError, ValueError, TypeError) as e:
            print(f"[Config] Corrupt settings in DB, using defaults: {e}")

    # Empty DB — try JSON migration
    if json_path is None:
        json_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "photoframe_settings.json",
        )
    if os.path.exists(json_path):
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                saved = json.load(f)
            merged = _deep_merge(get_default_settings(), saved)
            # Auto-enable weather when coordinates are configured
            lat = merged.get("open_meteo", {}).get("latitude", "")
            lon = merged.get("open_meteo", {}).get("longitude", "")
            if lat and lon:
                merged.setdefault("ui", {})["show_weather"] = True
            save_settings(merged)
            print(f"[Config] Migrated settings from {json_path}")
            try:
                migrated_path = json_path + ".migrated"
                os.rename(json_path, migrated_path)
                print(f"[Config] Renamed {json_path} → {migrated_path}")
            except Exception as rename_err:
                print(f"[Config] Could not rename settings JSON: {rename_err}")
            return merged
        except Exception as e:
            print(f"[Config] Migration failed: {e}")

    defaults = get_default_settings()
    save_settings(defaults)
    return defaults


def save_settings(data: dict[str, Any]) -> None:
    """Persist settings to DB and touch sentinel to trigger hot reload."""
    blob = json.dumps(data, indent=2)
    now = time.time()
    conn = _get_db()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS app_settings (
                key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at REAL
            )
        """)
        conn.execute(
            "INSERT OR REPLACE INTO app_settings (key, value, updated_at) VALUES ('main', ?, ?)",
            (blob, now),
        )
        conn.commit()
    finally:
        conn.close()

    sentinel = _get_sentinel_path()
    try:
        sentinel_dir = os.path.dirname(os.path.abspath(sentinel))
        os.makedirs(sentinel_dir, exist_ok=True)
        with open(sentinel, "w") as f:
            f.write(str(now))
    except Exception as e:
        print(f"[Config] Could not touch sentinel {sentinel}: {e}")
