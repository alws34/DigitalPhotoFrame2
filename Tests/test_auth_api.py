"""Tests for /api/auth endpoints: signup, login, logout, reset-password, /me."""
from unittest.mock import MagicMock

import pytest
from flask import Flask

from WebAPI.routes.auth import auth_bp

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_DEFAULT_USER = {"uid": "uid-1", "username": "alice", "role": "user", "is_active": True}


def _make_backend(
    *,
    allow_signup=True,
    allow_login=True,
    user_row=_DEFAULT_USER,
    create_uid="uid-1",
):
    """Build a minimal backend mock for auth routes."""
    backend = MagicMock()
    backend._rl_signup.allow.return_value = allow_signup
    backend._rl_login.allow.return_value = allow_login
    backend._client_ip.return_value = "127.0.0.1"
    backend._users.verify_login.return_value = user_row
    backend._users.create_user.return_value = create_uid
    return backend


@pytest.fixture()
def app():
    a = Flask(__name__)
    a.testing = True
    a.secret_key = "test-secret"
    a.config["backend"] = _make_backend()
    a.register_blueprint(auth_bp)
    return a


@pytest.fixture()
def client(app):
    return app.test_client()


# ---------------------------------------------------------------------------
# POST /api/auth/signup
# ---------------------------------------------------------------------------


def test_signup_valid(client):
    r = client.post("/api/auth/signup", json={
        "email": "alice@example.com",
        "username": "alice",
        "password": "Secret123!",
    })
    assert r.status_code == 201
    assert "uid" in r.get_json()


def test_signup_weak_password(client):
    r = client.post("/api/auth/signup", json={
        "email": "alice@example.com",
        "username": "alice",
        "password": "short",
    })
    assert r.status_code == 400
    assert "policy" in r.get_json()["error"].lower()


def test_signup_invalid_email(client):
    r = client.post("/api/auth/signup", json={
        "email": "not-an-email",
        "username": "alice",
        "password": "Secret123!",
    })
    assert r.status_code == 400


def test_signup_rate_limited(app):
    app.config["backend"] = _make_backend(allow_signup=False)
    r = app.test_client().post("/api/auth/signup", json={
        "email": "alice@example.com", "username": "alice", "password": "Secret123!",
    })
    assert r.status_code == 429


def test_signup_duplicate_user(app):
    backend = _make_backend()
    backend._users.create_user.side_effect = ValueError("Email already registered.")
    app.config["backend"] = backend
    r = app.test_client().post("/api/auth/signup", json={
        "email": "alice@example.com", "username": "alice", "password": "Secret123!",
    })
    assert r.status_code == 400
    assert "already" in r.get_json()["error"].lower()


# ---------------------------------------------------------------------------
# POST /api/auth/login
# ---------------------------------------------------------------------------


def test_login_valid(client):
    r = client.post("/api/auth/login", json={"username": "alice", "password": "Secret123!"})
    assert r.status_code == 200
    body = r.get_json()
    assert body["user"]["username"] == "alice"


def test_login_invalid_credentials(app):
    backend = _make_backend(user_row=None)
    app.config["backend"] = backend
    r = app.test_client().post("/api/auth/login", json={"username": "alice", "password": "wrong"})
    assert r.status_code == 401


def test_login_inactive_user(app):
    backend = _make_backend(
        user_row={"uid": "uid-1", "username": "alice", "role": "user", "is_active": False}
    )
    app.config["backend"] = backend
    r = app.test_client().post("/api/auth/login", json={"username": "alice", "password": "Secret123!"})
    assert r.status_code == 401


def test_login_rate_limited(app):
    app.config["backend"] = _make_backend(allow_login=False)
    r = app.test_client().post("/api/auth/login", json={"username": "alice", "password": "Secret123!"})
    assert r.status_code == 429


def test_login_sets_session(app):
    client = app.test_client()
    client.post("/api/auth/login", json={"username": "alice", "password": "Secret123!"})
    # verify session was populated via the mock _rotate_session call
    app.config["backend"]._rotate_session.assert_called_once()


# ---------------------------------------------------------------------------
# POST /api/auth/logout
# ---------------------------------------------------------------------------


def test_logout_clears_session(app):
    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess["user"] = "alice"
            sess["uid"] = "uid-1"
        r = c.post("/api/auth/logout")
        assert r.status_code == 200
        with c.session_transaction() as sess:
            assert "user" not in sess


# ---------------------------------------------------------------------------
# GET /api/auth/me
# ---------------------------------------------------------------------------


def test_me_unauthenticated(app):
    backend = _make_backend()
    backend.is_authenticated.return_value = False
    app.config["backend"] = backend
    r = app.test_client().get("/api/auth/me")
    assert r.status_code == 401


def test_me_authenticated(app):
    backend = _make_backend()
    backend.is_authenticated.return_value = True
    app.config["backend"] = backend
    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess["user"] = "alice"
            sess["uid"] = "uid-1"
            sess["role"] = "user"
        r = c.get("/api/auth/me")
        assert r.status_code == 200
        body = r.get_json()
        assert body["username"] == "alice"
        assert body["uid"] == "uid-1"
