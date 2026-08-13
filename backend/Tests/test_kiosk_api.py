"""Tests for the in-process kiosk token mint + /api/kiosk/login exchange."""
from unittest.mock import MagicMock, patch

import pytest
from flask import Flask

import WebAPI.routes.kiosk as kiosk_mod
from WebAPI.routes.kiosk import kiosk_bp, mint_kiosk_token


@pytest.fixture(autouse=True)
def _clear_tokens():
    kiosk_mod._tokens.clear()
    yield
    kiosk_mod._tokens.clear()


@pytest.fixture()
def app():
    a = Flask(__name__)
    a.testing = True
    a.secret_key = "test"
    backend = MagicMock()
    a.config["backend"] = backend
    a.register_blueprint(kiosk_bp)
    return a


@pytest.fixture()
def client(app):
    return app.test_client()


def test_mint_returns_unique_unguessable_tokens():
    a = mint_kiosk_token()
    b = mint_kiosk_token()
    assert a != b
    assert len(a) > 20
    assert a in kiosk_mod._tokens
    assert b in kiosk_mod._tokens


def test_login_no_token_redirects_without_session(client, app):
    r = client.get("/api/kiosk/login")
    assert r.status_code == 302
    assert r.headers["Location"] == "/settings"
    app.config["backend"]._rotate_session.assert_not_called()


def test_login_unknown_token_redirects_without_session(client, app):
    r = client.get("/api/kiosk/login?token=not-a-real-token")
    assert r.status_code == 302
    app.config["backend"]._rotate_session.assert_not_called()


def test_login_valid_token_rotates_session_and_redirects(client, app):
    token = mint_kiosk_token()
    owner = {"uid": "u1", "username": "alon", "role": "user"}
    with patch("WebAPI.routes.kiosk.get_device_owner", return_value=owner):
        r = client.get(f"/api/kiosk/login?token={token}")
    assert r.status_code == 302
    assert r.headers["Location"] == "/settings"
    app.config["backend"]._rotate_session.assert_called_once_with("alon", "u1", "user")


def test_login_token_is_single_use(client, app):
    token = mint_kiosk_token()
    owner = {"uid": "u1", "username": "alon", "role": "user"}
    with patch("WebAPI.routes.kiosk.get_device_owner", return_value=owner):
        client.get(f"/api/kiosk/login?token={token}")
        app.config["backend"]._rotate_session.reset_mock()
        client.get(f"/api/kiosk/login?token={token}")
    app.config["backend"]._rotate_session.assert_not_called()


def test_login_expired_token_does_not_authenticate(client, app):
    token = mint_kiosk_token()
    kiosk_mod._tokens[token] = 0.0  # force expiry into the past
    with patch("WebAPI.routes.kiosk.get_device_owner", return_value={"uid": "u1", "username": "alon", "role": "user"}):
        r = client.get(f"/api/kiosk/login?token={token}")
    assert r.status_code == 302
    app.config["backend"]._rotate_session.assert_not_called()


def test_login_no_device_owner_fails_soft(client, app):
    token = mint_kiosk_token()
    with patch("WebAPI.routes.kiosk.get_device_owner", return_value=None):
        r = client.get(f"/api/kiosk/login?token={token}")
    assert r.status_code == 302
    app.config["backend"]._rotate_session.assert_not_called()
