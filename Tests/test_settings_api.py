"""Tests for /api/settings endpoints: GET, POST, schema, system_stats, logs."""
from unittest.mock import MagicMock, mock_open, patch

import pytest
from flask import Flask

from WebAPI.routes.settings import settings_bp

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_backend(*, authenticated=True, settings=None):
    backend = MagicMock()
    backend.is_authenticated.return_value = authenticated
    backend.load_settings.return_value = settings or {
        "system": {"image_dir": "/data/images"},
        "backend_configs": {"stream_fps": 10},
    }
    backend.save_settings.return_value = None
    backend.LOG_FILE_PATH = "/tmp/test_photoframe.log"
    return backend


@pytest.fixture()
def app():
    a = Flask(__name__)
    a.testing = True
    a.secret_key = "test"
    a.config["backend"] = _make_backend()
    a.register_blueprint(settings_bp)
    return a


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def unauth_client(app):
    app.config["backend"] = _make_backend(authenticated=False)
    return app.test_client()


# ---------------------------------------------------------------------------
# GET /api/settings
# ---------------------------------------------------------------------------


def test_get_settings_ok(client):
    r = client.get("/api/settings")
    assert r.status_code == 200
    data = r.get_json()
    assert "system" in data
    assert "backend_configs" in data


def test_get_settings_unauth(unauth_client):
    r = unauth_client.get("/api/settings")
    assert r.status_code == 401


def test_get_settings_returns_dict(client):
    r = client.get("/api/settings")
    assert isinstance(r.get_json(), dict)


def test_get_settings_backend_error(app):
    backend = _make_backend()
    backend.load_settings.side_effect = RuntimeError("disk error")
    app.config["backend"] = backend
    r = app.test_client().get("/api/settings")
    assert r.status_code == 500


# ---------------------------------------------------------------------------
# POST /api/settings
# ---------------------------------------------------------------------------


def test_post_settings_ok(client):
    r = client.post("/api/settings", json={"system": {"image_dir": "/new"}})
    assert r.status_code == 200
    assert "updated" in r.get_json()["message"].lower()


def test_post_settings_calls_save(app):
    payload = {"system": {"image_dir": "/new"}}
    app.test_client().post("/api/settings", json=payload)
    app.config["backend"].save_settings.assert_called_once_with(payload)


def test_post_settings_unauth(unauth_client):
    r = unauth_client.post("/api/settings", json={"system": {}})
    assert r.status_code == 401


def test_post_settings_invalid_json(client):
    r = client.post(
        "/api/settings",
        data="not json",
        content_type="application/json",
    )
    assert r.status_code == 400


def test_post_settings_non_dict_payload(client):
    r = client.post("/api/settings", json=[1, 2, 3])
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# GET /api/settings/schema
# ---------------------------------------------------------------------------


def test_get_schema_ok(client):
    r = client.get("/api/settings/schema")
    assert r.status_code == 200
    assert isinstance(r.get_json(), dict)


def test_get_schema_unauth(unauth_client):
    r = unauth_client.get("/api/settings/schema")
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# GET /api/settings/system_stats
# ---------------------------------------------------------------------------


def test_system_stats_ok(client):
    r = client.get("/api/settings/system_stats")
    assert r.status_code == 200
    body = r.get_json()
    assert "cpu_usage" in body
    assert "ram_percent" in body


def test_system_stats_unauth(unauth_client):
    r = unauth_client.get("/api/settings/system_stats")
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# GET /api/settings/logs
# ---------------------------------------------------------------------------


def test_get_logs_ok(app):
    log_content = "line1\nline2\n"
    with patch("builtins.open", mock_open(read_data=log_content)):
        r = app.test_client().get("/api/settings/logs")
    assert r.status_code == 200
    body = r.get_json()
    assert "logs" in body


def test_get_logs_not_found(app):
    with patch("builtins.open", side_effect=FileNotFoundError):
        r = app.test_client().get("/api/settings/logs")
    assert r.status_code == 404


def test_get_logs_unauth(unauth_client):
    r = unauth_client.get("/api/settings/logs")
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# POST /api/settings/clear_logs
# ---------------------------------------------------------------------------


def test_clear_logs_ok(app):
    with patch("builtins.open", mock_open()):
        r = app.test_client().post("/api/settings/clear_logs")
    assert r.status_code == 200


def test_clear_logs_unauth(unauth_client):
    r = unauth_client.post("/api/settings/clear_logs")
    assert r.status_code == 401
