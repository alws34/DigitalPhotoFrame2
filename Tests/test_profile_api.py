"""Tests for /api/profile/picture endpoints."""
import io
import os
import tempfile
import unittest.mock as mock

import pytest

# Minimal Flask app fixture that registers only the profile blueprint
@pytest.fixture()
def profile_app(tmp_path):
    """Create a minimal Flask app with the profile blueprint registered."""
    from flask import Flask
    from WebAPI.routes.profile import profile_bp

    app = Flask(__name__)
    app.testing = True

    # Fake backend with is_authenticated always True
    class _FakeBackend:
        def is_authenticated(self):
            return True

    app.config["backend"] = _FakeBackend()
    app.register_blueprint(profile_bp)

    profile_path = str(tmp_path / "profile.png")
    with mock.patch("WebAPI.routes.profile._profile_path", return_value=profile_path):
        yield app, profile_path


@pytest.fixture()
def client(profile_app):
    app, _ = profile_app
    return app.test_client(), profile_app[1]


# ---------------------------------------------------------------------------
# GET /api/profile/picture
# ---------------------------------------------------------------------------

def test_get_picture_not_found(client):
    c, _ = client
    resp = c.get("/api/profile/picture")
    assert resp.status_code == 404


def test_get_picture_returns_file(tmp_path, profile_app):
    app, profile_path = profile_app

    # Write a fake PNG
    fake_png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 20
    with open(profile_path, "wb") as f:
        f.write(fake_png)

    with mock.patch("WebAPI.routes.profile._profile_path", return_value=profile_path):
        with app.test_client() as c:
            resp = c.get("/api/profile/picture")
    assert resp.status_code == 200
    assert resp.content_type == "image/png"
    assert resp.data == fake_png


# ---------------------------------------------------------------------------
# POST /api/profile/picture
# ---------------------------------------------------------------------------

def test_upload_picture_saves_file(tmp_path, profile_app):
    app, profile_path = profile_app
    fake_png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 20

    with mock.patch("WebAPI.routes.profile._profile_path", return_value=profile_path):
        with app.test_client() as c:
            data = {"file": (io.BytesIO(fake_png), "profile.png")}
            resp = c.post("/api/profile/picture", data=data, content_type="multipart/form-data")

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["success"] is True
    assert os.path.exists(profile_path)
    with open(profile_path, "rb") as f:
        assert f.read() == fake_png


def test_upload_picture_no_file_returns_400(profile_app):
    app, profile_path = profile_app
    with mock.patch("WebAPI.routes.profile._profile_path", return_value=profile_path):
        with app.test_client() as c:
            resp = c.post("/api/profile/picture", data={}, content_type="multipart/form-data")
    assert resp.status_code == 400


def test_upload_picture_unsupported_extension(profile_app):
    app, profile_path = profile_app
    with mock.patch("WebAPI.routes.profile._profile_path", return_value=profile_path):
        with app.test_client() as c:
            data = {"file": (io.BytesIO(b"data"), "file.exe")}
            resp = c.post("/api/profile/picture", data=data, content_type="multipart/form-data")
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# DELETE /api/profile/picture
# ---------------------------------------------------------------------------

def test_delete_picture_removes_file(tmp_path, profile_app):
    app, profile_path = profile_app

    with open(profile_path, "wb") as f:
        f.write(b"PNG_DATA")

    with mock.patch("WebAPI.routes.profile._profile_path", return_value=profile_path):
        with app.test_client() as c:
            resp = c.delete("/api/profile/picture")

    assert resp.status_code == 200
    assert not os.path.exists(profile_path)


def test_delete_picture_nonexistent_returns_200(profile_app):
    app, profile_path = profile_app
    assert not os.path.exists(profile_path)
    with mock.patch("WebAPI.routes.profile._profile_path", return_value=profile_path):
        with app.test_client() as c:
            resp = c.delete("/api/profile/picture")
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Auth check
# ---------------------------------------------------------------------------

def test_upload_requires_auth(tmp_path):
    from flask import Flask
    from WebAPI.routes.profile import profile_bp

    app = Flask(__name__)
    app.testing = True

    class _Unauthenticated:
        def is_authenticated(self):
            return False

    app.config["backend"] = _Unauthenticated()
    app.register_blueprint(profile_bp)
    profile_path = str(tmp_path / "profile.png")

    with mock.patch("WebAPI.routes.profile._profile_path", return_value=profile_path):
        with app.test_client() as c:
            data = {"file": (io.BytesIO(b"PNG"), "profile.png")}
            resp = c.post("/api/profile/picture", data=data, content_type="multipart/form-data")

    assert resp.status_code == 401


def test_delete_requires_auth(tmp_path):
    from flask import Flask
    from WebAPI.routes.profile import profile_bp

    app = Flask(__name__)
    app.testing = True

    class _Unauthenticated:
        def is_authenticated(self):
            return False

    app.config["backend"] = _Unauthenticated()
    app.register_blueprint(profile_bp)
    profile_path = str(tmp_path / "profile.png")

    with mock.patch("WebAPI.routes.profile._profile_path", return_value=profile_path):
        with app.test_client() as c:
            resp = c.delete("/api/profile/picture")

    assert resp.status_code == 401
