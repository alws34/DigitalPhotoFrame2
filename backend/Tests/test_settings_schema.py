import importlib


def _get_cs():
    import Utilities.config_store as cs
    importlib.reload(cs)
    return cs


def test_schema_covers_all_default_sections():
    cs = _get_cs()
    defaults = cs.get_default_settings()
    schema = cs.SETTINGS_SCHEMA
    for section in defaults:
        if section == "about":
            continue
        assert section in schema, f"Section '{section}' missing from SETTINGS_SCHEMA"


def test_get_field_schema_simple_path():
    cs = _get_cs()
    desc = cs.get_field_schema("playback.animation_fps")
    assert desc is not None
    assert desc["type"] == "int"
    assert desc["min"] == 1
    assert desc["max"] == 60


def test_get_field_schema_nested_path():
    cs = _get_cs()
    desc = cs.get_field_schema("ui.margins.left")
    assert desc is not None
    assert desc["type"] == "int"


def test_get_field_schema_unknown_path_returns_none():
    cs = _get_cs()
    assert cs.get_field_schema("nonexistent.field") is None


def test_get_field_schema_section_node_returns_none():
    cs = _get_cs()
    # "ui.margins" is a container, not a leaf — should return None
    assert cs.get_field_schema("ui.margins") is None


def test_get_restart_required_paths_contains_known_fields():
    cs = _get_cs()
    rr = cs.get_restart_required_paths()
    assert "backend_configs.server_port" in rr
    assert "backend_configs.stream_width" in rr
    assert "backend_configs.supersecretkey" in rr


def test_get_restart_required_paths_excludes_non_restart_fields():
    cs = _get_cs()
    rr = cs.get_restart_required_paths()
    assert "playback.animation_fps" not in rr
    assert "ui.time_font_size" not in rr


def test_schema_password_type():
    cs = _get_cs()
    desc = cs.get_field_schema("backend_configs.supersecretkey")
    assert desc["type"] == "password"


def test_schema_enum_has_choices():
    cs = _get_cs()
    desc = cs.get_field_schema("open_meteo.temperature_unit")
    assert desc["type"] == "enum"
    assert "celsius" in desc["choices"]
    assert "fahrenheit" in desc["choices"]


def test_system_timezone_in_defaults():
    cs = _get_cs()
    defaults = cs.get_default_settings()
    assert "timezone" in defaults["system"]
    assert defaults["system"]["timezone"] == "System Default"


def test_system_timezone_schema_is_enum_with_choices():
    cs = _get_cs()
    desc = cs.get_field_schema("system.timezone")
    assert desc is not None
    assert desc["type"] == "enum"
    assert "System Default" in desc["choices"]
    assert "Asia/Jerusalem" in desc["choices"]
    assert desc["restart_required"] is False


def test_apply_system_timezone_sets_env():
    import os
    import time
    cs = _get_cs()
    cs.apply_system_timezone({"system": {"timezone": "Asia/Jerusalem"}})
    assert os.environ.get("TZ") == "Asia/Jerusalem"
    os.environ.pop("TZ", None)
    time.tzset()


def test_apply_system_timezone_clears_env_on_system_default():
    import os
    cs = _get_cs()
    os.environ["TZ"] = "US/Eastern"
    cs.apply_system_timezone({"system": {"timezone": "System Default"}})
    assert "TZ" not in os.environ


def test_apply_system_timezone_clears_env_on_empty():
    import os
    cs = _get_cs()
    os.environ["TZ"] = "US/Eastern"
    cs.apply_system_timezone({"system": {"timezone": ""}})
    assert "TZ" not in os.environ
