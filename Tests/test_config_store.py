import importlib
import json
import sqlite3


def test_app_settings_table_created(tmp_path, monkeypatch):
    monkeypatch.setenv("PF_DB_PATH", str(tmp_path / "test.db"))
    # Force reimport so env var is picked up
    import WebAPI.database as db_mod
    importlib.reload(db_mod)
    db_mod.init_db()
    conn = sqlite3.connect(str(tmp_path / "test.db"))
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    conn.close()
    assert "app_settings" in tables


def test_migrate_settings_if_needed_loads_json(tmp_path, monkeypatch):
    monkeypatch.setenv("PF_DB_PATH", str(tmp_path / "test.db"))
    json_path = tmp_path / "settings.json"
    json_path.write_text(json.dumps({"playback": {"animation_fps": 20}}))
    import WebAPI.database as db_mod
    importlib.reload(db_mod)
    db_mod.init_db()
    db_mod.migrate_settings_if_needed(str(json_path))
    conn = sqlite3.connect(str(tmp_path / "test.db"))
    row = conn.execute("SELECT value FROM app_settings WHERE key = 'main'").fetchone()
    conn.close()
    assert row is not None
    data = json.loads(row[0])
    assert data["playback"]["animation_fps"] == 20

def test_migrate_settings_if_needed_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv("PF_DB_PATH", str(tmp_path / "test.db"))
    json_path = tmp_path / "settings.json"
    json_path.write_text(json.dumps({"playback": {"animation_fps": 20}}))
    import WebAPI.database as db_mod
    importlib.reload(db_mod)
    db_mod.init_db()
    db_mod.migrate_settings_if_needed(str(json_path))
    # Call again — should not raise and should not duplicate
    db_mod.migrate_settings_if_needed(str(json_path))
    conn = sqlite3.connect(str(tmp_path / "test.db"))
    count = conn.execute("SELECT COUNT(*) FROM app_settings").fetchone()[0]
    conn.close()
    assert count == 1

def test_migrate_settings_if_needed_missing_file(tmp_path, monkeypatch):
    monkeypatch.setenv("PF_DB_PATH", str(tmp_path / "test.db"))
    import WebAPI.database as db_mod
    importlib.reload(db_mod)
    db_mod.init_db()
    # Should not raise even if file doesn't exist
    db_mod.migrate_settings_if_needed(str(tmp_path / "nonexistent.json"))
    conn = sqlite3.connect(str(tmp_path / "test.db"))
    count = conn.execute("SELECT COUNT(*) FROM app_settings").fetchone()[0]
    conn.close()
    assert count == 0


def test_load_returns_defaults_when_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("PF_DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("PF_SENTINEL_PATH", str(tmp_path / "sentinel"))
    import WebAPI.database as db_mod
    importlib.reload(db_mod)
    db_mod.init_db()
    import Utilities.config_store as cs
    importlib.reload(cs)
    data = cs.load_settings()
    assert "playback" in data
    assert "ui" in data
    assert data["playback"]["animation_fps"] == 30

def test_save_and_load_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("PF_DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("PF_SENTINEL_PATH", str(tmp_path / "sentinel"))
    import WebAPI.database as db_mod
    importlib.reload(db_mod)
    db_mod.init_db()
    import Utilities.config_store as cs
    importlib.reload(cs)
    settings = cs.get_default_settings()
    settings["playback"]["animation_fps"] = 25
    cs.save_settings(settings)
    loaded = cs.load_settings()
    assert loaded["playback"]["animation_fps"] == 25

def test_sentinel_touched_on_save(tmp_path, monkeypatch):
    monkeypatch.setenv("PF_DB_PATH", str(tmp_path / "test.db"))
    sentinel = tmp_path / "sentinel"
    monkeypatch.setenv("PF_SENTINEL_PATH", str(sentinel))
    import WebAPI.database as db_mod
    importlib.reload(db_mod)
    db_mod.init_db()
    import Utilities.config_store as cs
    importlib.reload(cs)
    cs.save_settings(cs.get_default_settings())
    assert sentinel.exists()

def test_migrate_from_json(tmp_path, monkeypatch):
    monkeypatch.setenv("PF_DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("PF_SENTINEL_PATH", str(tmp_path / "sentinel"))
    json_path = tmp_path / "photoframe_settings.json"
    json_path.write_text(json.dumps({"playback": {"animation_fps": 20}}))
    import WebAPI.database as db_mod
    importlib.reload(db_mod)
    db_mod.init_db()
    import Utilities.config_store as cs
    importlib.reload(cs)
    data = cs.load_settings(json_path=str(json_path))
    assert data["playback"]["animation_fps"] == 20

def test_deep_merge_preserves_defaults_for_missing_keys(tmp_path, monkeypatch):
    monkeypatch.setenv("PF_DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("PF_SENTINEL_PATH", str(tmp_path / "sentinel"))
    import WebAPI.database as db_mod
    importlib.reload(db_mod)
    db_mod.init_db()
    import Utilities.config_store as cs
    importlib.reload(cs)
    partial = {"playback": {"animation_fps": 15}}
    cs.save_settings(partial)
    loaded = cs.load_settings()
    # Default key present even though not in saved partial
    assert "ui" in loaded
    assert loaded["playback"]["animation_fps"] == 15


def test_notify_fires_all_callbacks(tmp_path, monkeypatch):
    monkeypatch.setenv("PF_DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("PF_SENTINEL_PATH", str(tmp_path / "sentinel"))
    import Utilities.config_events as ce
    importlib.reload(ce)
    received = []
    ce.on_settings_changed(lambda d: received.append(d))
    ce.notify_settings_changed({"playback": {"animation_fps": 99}})
    assert len(received) == 1
    assert received[0]["playback"]["animation_fps"] == 99

def test_callback_exception_does_not_stop_others(tmp_path, monkeypatch):
    monkeypatch.setenv("PF_DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("PF_SENTINEL_PATH", str(tmp_path / "sentinel"))
    import Utilities.config_events as ce
    importlib.reload(ce)
    called = []
    def bad_cb(d): raise RuntimeError("boom")
    ce.on_settings_changed(bad_cb)
    ce.on_settings_changed(lambda d: called.append(1))
    ce.notify_settings_changed({})
    assert called == [1]  # Second callback still ran
