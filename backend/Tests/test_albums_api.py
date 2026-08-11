"""Tests for /api/albums endpoints: active album, list, subscribe, unsubscribe."""
from unittest.mock import MagicMock

import pytest
from flask import Flask

from WebAPI.routes.albums import albums_bp

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_ALBUMS = [
    {"id": "album-1", "name": "Vacation"},
    {"id": "album-2", "name": "Family"},
]


def _make_backend(*, authenticated=True, has_manager=True, albums=None, active_id="all"):
    backend = MagicMock()
    backend.is_authenticated.return_value = authenticated

    if has_manager:
        am = MagicMock()
        am.get_albums.return_value = albums if albums is not None else _ALBUMS
        am.get_active_album_id.return_value = active_id
        am.subscribe_album.return_value = "album-new"
        backend.album_manager = am
    else:
        backend.album_manager = None

    return backend


@pytest.fixture()
def app():
    a = Flask(__name__)
    a.testing = True
    a.secret_key = "test"
    a.config["backend"] = _make_backend()
    a.register_blueprint(albums_bp)
    return a


@pytest.fixture()
def client(app):
    return app.test_client()


# ---------------------------------------------------------------------------
# GET /api/albums/active
# ---------------------------------------------------------------------------


def test_get_active_album_all(client):
    r = client.get("/api/albums/active")
    assert r.status_code == 200
    body = r.get_json()
    assert body["album_id"] == "all"
    assert "name" in body


def test_get_active_album_specific(app):
    app.config["backend"] = _make_backend(active_id="album-1")
    r = app.test_client().get("/api/albums/active")
    assert r.status_code == 200
    body = r.get_json()
    assert body["album_id"] == "album-1"
    assert body["name"] == "Vacation"


def test_get_active_album_unauth(app):
    app.config["backend"] = _make_backend(authenticated=False)
    r = app.test_client().get("/api/albums/active")
    assert r.status_code == 401


def test_get_active_album_no_manager(app):
    app.config["backend"] = _make_backend(has_manager=False)
    r = app.test_client().get("/api/albums/active")
    assert r.status_code == 503


# ---------------------------------------------------------------------------
# PUT /api/albums/active
# ---------------------------------------------------------------------------


def test_set_active_album(client):
    r = client.put("/api/albums/active", json={"album_id": "album-1"})
    assert r.status_code == 200
    body = r.get_json()
    assert "album_id" in body


def test_set_active_album_calls_manager(app):
    app.test_client().put("/api/albums/active", json={"album_id": "album-2"})
    app.config["backend"].album_manager.set_active_album.assert_called_once_with("album-2")


def test_set_active_album_defaults_to_all(client):
    r = client.put("/api/albums/active", json={})
    assert r.status_code == 200
    app = client.application
    app.config["backend"].album_manager.set_active_album.assert_called_with("all")


def test_set_active_album_unauth(app):
    app.config["backend"] = _make_backend(authenticated=False)
    r = app.test_client().put("/api/albums/active", json={"album_id": "album-1"})
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# GET /api/albums/
# ---------------------------------------------------------------------------


def test_list_albums(client):
    r = client.get("/api/albums/")
    assert r.status_code == 200
    body = r.get_json()
    assert isinstance(body, list)
    assert len(body) == 2
    assert body[0]["name"] == "Vacation"


def test_list_albums_empty(app):
    app.config["backend"] = _make_backend(albums=[])
    r = app.test_client().get("/api/albums/")
    assert r.status_code == 200
    assert r.get_json() == []


def test_list_albums_unauth(app):
    app.config["backend"] = _make_backend(authenticated=False)
    r = app.test_client().get("/api/albums/")
    assert r.status_code == 401


def test_list_albums_no_manager(app):
    app.config["backend"] = _make_backend(has_manager=False)
    r = app.test_client().get("/api/albums/")
    assert r.status_code == 503


# ---------------------------------------------------------------------------
# POST /api/albums/
# ---------------------------------------------------------------------------


def test_subscribe_album(client):
    r = client.post("/api/albums/", json={
        "source_id": "src-1", "remote_id": "remote-1", "name": "New Album",
    })
    assert r.status_code == 201


def test_subscribe_album_calls_manager(app):
    app.test_client().post("/api/albums/", json={
        "source_id": "src-1", "remote_id": "remote-1", "name": "New Album",
    })
    am = app.config["backend"].album_manager
    am.subscribe_album.assert_called_once_with("src-1", "remote-1", "New Album")


def test_subscribe_album_unauth(app):
    app.config["backend"] = _make_backend(authenticated=False)
    r = app.test_client().post("/api/albums/", json={"source_id": "s", "remote_id": "r", "name": "n"})
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# DELETE /api/albums/<album_id>
# ---------------------------------------------------------------------------


def test_unsubscribe_album(client):
    r = client.delete("/api/albums/album-1")
    assert r.status_code == 204
    assert r.data == b""


def test_unsubscribe_album_not_found(app):
    am = app.config["backend"].album_manager
    am.unsubscribe_album.side_effect = KeyError("album-1")
    r = app.test_client().delete("/api/albums/album-1")
    assert r.status_code == 404


def test_unsubscribe_album_unauth(app):
    app.config["backend"] = _make_backend(authenticated=False)
    r = app.test_client().delete("/api/albums/album-1")
    assert r.status_code == 401
