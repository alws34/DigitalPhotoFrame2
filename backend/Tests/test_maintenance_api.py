"""Tests for /api/maintenance endpoints: restart."""
from unittest.mock import MagicMock, patch

import pytest
from flask import Flask

from WebAPI.routes.maintenance import maintenance_bp


@pytest.fixture()
def app():
    a = Flask(__name__)
    a.testing = True
    a.secret_key = "test"
    backend = MagicMock()
    backend.is_authenticated.return_value = True
    a.config["backend"] = backend
    a.config["restart_fn"] = MagicMock()
    a.register_blueprint(maintenance_bp)
    return a


@pytest.fixture()
def client(app):
    return app.test_client()


def test_restart_unauth(app):
    app.config["backend"].is_authenticated.return_value = False
    r = app.test_client().post("/api/maintenance/restart")
    assert r.status_code == 401


def test_restart_not_configured(app):
    app.config["restart_fn"] = None
    r = app.test_client().post("/api/maintenance/restart")
    assert r.status_code == 501


def test_restart_ok_triggers_restart_fn(app):
    with patch("WebAPI.routes.maintenance.threading.Thread") as mock_thread_cls, \
         patch("WebAPI.routes.maintenance.time.sleep"):
        r = app.test_client().post("/api/maintenance/restart")
        assert r.status_code == 202
        assert "restart" in r.get_json()["message"].lower()

        # Run the thread target synchronously to verify it calls restart_fn.
        _, kwargs = mock_thread_cls.call_args
        kwargs["target"]()
    app.config["restart_fn"].assert_called_once()


def test_restart_logs_failure(app):
    app.config["restart_fn"].side_effect = RuntimeError("boom")
    with patch("WebAPI.routes.maintenance.threading.Thread") as mock_thread_cls, \
         patch("WebAPI.routes.maintenance.time.sleep"):
        app.test_client().post("/api/maintenance/restart")
        _, kwargs = mock_thread_cls.call_args
        # Should not raise even though restart_fn blows up.
        kwargs["target"]()
    app.config["restart_fn"].assert_called_once()


def test_restart_no_backend_configured(app):
    app.config["backend"] = None
    with patch("WebAPI.routes.maintenance.threading.Thread"), \
         patch("WebAPI.routes.maintenance.time.sleep"):
        r = app.test_client().post("/api/maintenance/restart")
    assert r.status_code == 202
