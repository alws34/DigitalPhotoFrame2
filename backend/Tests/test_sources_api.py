"""Tests for /api/sources endpoints: list/add/remove sources, sync, remote albums, OAuth."""
from unittest.mock import MagicMock, patch

import pytest
from flask import Flask

from WebAPI.routes.sources import sources_bp


def _make_backend(*, authenticated=True, album_manager=True):
    backend = MagicMock()
    backend.is_authenticated.return_value = authenticated
    backend.album_manager = MagicMock() if album_manager else None
    return backend


@pytest.fixture()
def app():
    a = Flask(__name__)
    a.testing = True
    a.secret_key = "test"
    a.config["backend"] = _make_backend()
    a.register_blueprint(sources_bp)
    return a


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def unauth_client(app):
    app.config["backend"] = _make_backend(authenticated=False)
    return app.test_client()


@pytest.fixture()
def no_am_client(app):
    app.config["backend"] = _make_backend(album_manager=False)
    return app.test_client()


# ---------------------------------------------------------------------------
# GET /api/sources/
# ---------------------------------------------------------------------------


def test_list_sources_ok(client, app):
    app.config["backend"].album_manager.get_sources.return_value = [{"id": "1"}]
    r = client.get("/api/sources/")
    assert r.status_code == 200
    assert r.get_json() == [{"id": "1"}]


def test_list_sources_unauth(unauth_client):
    r = unauth_client.get("/api/sources/")
    assert r.status_code == 401


def test_list_sources_no_album_manager(no_am_client):
    r = no_am_client.get("/api/sources/")
    assert r.status_code == 503


def test_list_sources_backend_error(client, app):
    app.config["backend"].album_manager.get_sources.side_effect = RuntimeError("boom")
    r = client.get("/api/sources/")
    assert r.status_code == 500


# ---------------------------------------------------------------------------
# POST /api/sources/
# ---------------------------------------------------------------------------


def test_add_source_ok(client, app):
    am = app.config["backend"].album_manager
    am.add_source.return_value = "src1"
    am.get_sources.return_value = [{"id": "src1", "name": "My Immich"}]
    r = client.post(
        "/api/sources/",
        json={"type": "immich", "name": "My Immich", "config": {}, "credentials": {}},
    )
    assert r.status_code == 201
    assert r.get_json()["id"] == "src1"


def test_add_source_unauth(unauth_client):
    r = unauth_client.post("/api/sources/", json={})
    assert r.status_code == 401


def test_add_source_error(client, app):
    app.config["backend"].album_manager.add_source.side_effect = ValueError("bad type")
    r = client.post("/api/sources/", json={"type": "bogus"})
    assert r.status_code == 500


# ---------------------------------------------------------------------------
# DELETE /api/sources/<id>
# ---------------------------------------------------------------------------


def test_remove_source_ok(client, app):
    r = client.delete("/api/sources/src1")
    assert r.status_code == 204
    app.config["backend"].album_manager.remove_source.assert_called_once_with("src1")


def test_remove_source_not_found(client, app):
    app.config["backend"].album_manager.remove_source.side_effect = KeyError()
    r = client.delete("/api/sources/nope")
    assert r.status_code == 404


def test_remove_source_unauth(unauth_client):
    r = unauth_client.delete("/api/sources/src1")
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# POST /api/sources/<id>/sync
# ---------------------------------------------------------------------------


def test_trigger_sync_ok(client, app):
    r = client.post("/api/sources/src1/sync")
    assert r.status_code == 202
    app.config["backend"].album_manager.trigger_sync.assert_called_once_with("src1")


def test_trigger_sync_unauth(unauth_client):
    r = unauth_client.post("/api/sources/src1/sync")
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# GET /api/sources/<id>/remote-albums
# ---------------------------------------------------------------------------


def test_list_remote_albums_ok(client, app):
    album = MagicMock(remote_id="a1", media_count=42)
    album.name = "Vacation"
    app.config["backend"].album_manager.list_remote_albums.return_value = [album]
    r = client.get("/api/sources/src1/remote-albums")
    assert r.status_code == 200
    body = r.get_json()
    assert body[0]["remote_id"] == "a1"
    assert body[0]["media_count"] == 42


def test_list_remote_albums_unauth(unauth_client):
    r = unauth_client.get("/api/sources/src1/remote-albums")
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# POST /api/sources/<id>/auth/start
# ---------------------------------------------------------------------------


def test_auth_start_ok(client, app):
    with patch(
        "Utilities.sources.google_photos.GooglePhotosSource.get_auth_url",
        return_value="https://accounts.google.com/o/oauth2/auth?...",
    ), patch("WebAPI.database.get_db") as mock_get_db:
        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = None
        mock_get_db.return_value.__enter__.return_value = conn
        r = client.post(
            "/api/sources/src1/auth/start",
            json={"client_id": "cid", "client_secret": "secret", "redirect_uri": "http://x"},
        )
    assert r.status_code == 200
    assert "redirect_url" in r.get_json()


def test_auth_start_unauth(unauth_client):
    r = unauth_client.post("/api/sources/src1/auth/start", json={})
    assert r.status_code == 401


def test_auth_start_no_album_manager(no_am_client):
    r = no_am_client.post("/api/sources/src1/auth/start", json={})
    assert r.status_code == 503


# ---------------------------------------------------------------------------
# GET /api/sources/<id>/auth/callback
# ---------------------------------------------------------------------------


def test_auth_callback_ok(client, app):
    with patch(
        "Utilities.sources.google_photos.GooglePhotosSource.exchange_code",
        return_value={"access_token": "tok"},
    ), patch("WebAPI.database.get_db") as mock_get_db:
        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = {"config_json": "{}"}
        mock_get_db.return_value.__enter__.return_value = conn
        r = client.get("/api/sources/src1/auth/callback", query_string={"code": "abc"})
    assert r.status_code == 200
    assert b"Connected" in r.data
    app.config["backend"].album_manager.update_source_credentials.assert_called_once()


def test_auth_callback_no_album_manager(no_am_client):
    r = no_am_client.get("/api/sources/src1/auth/callback")
    assert r.status_code == 503
