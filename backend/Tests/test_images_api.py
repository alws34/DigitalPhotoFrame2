"""Tests for /api/images endpoints: list, metadata, upload, serve, delete, thumb."""
import io
from unittest.mock import MagicMock

import pytest
from flask import Flask

from WebAPI.routes.images import images_bp


def _make_backend(image_dir, *, authenticated=True):
    backend = MagicMock()
    backend.is_authenticated.return_value = authenticated
    backend.IMAGE_DIR = str(image_dir)
    backend.load_metadata_db.return_value = {}
    backend.latest_metadata = {}
    backend._metadata_lock = MagicMock()
    backend.allowed_file.side_effect = lambda fn: fn.lower().endswith((".jpg", ".png"))
    backend.compute_image_hash.return_value = "deadbeef"
    return backend


@pytest.fixture()
def image_dir(tmp_path):
    d = tmp_path / "images"
    d.mkdir()
    return d


@pytest.fixture()
def app(image_dir):
    a = Flask(__name__)
    a.testing = True
    a.secret_key = "test"
    a.config["backend"] = _make_backend(image_dir)
    a.register_blueprint(images_bp)
    return a


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def unauth_client(app, image_dir):
    app.config["backend"] = _make_backend(image_dir, authenticated=False)
    return app.test_client()


# ---------------------------------------------------------------------------
# GET /api/images/
# ---------------------------------------------------------------------------


def test_list_images_ok(app, image_dir):
    (image_dir / "a.jpg").write_bytes(b"x")
    app.config["backend"].get_images_from_directory.return_value = ["a.jpg"]
    r = app.test_client().get("/api/images/")
    assert r.status_code == 200
    data = r.get_json()
    assert data[0]["name"] == "a.jpg"


def test_list_images_unauth(unauth_client):
    r = unauth_client.get("/api/images/")
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# GET /api/images/current_metadata
# ---------------------------------------------------------------------------


def test_current_metadata_ok(client, app):
    app.config["backend"].latest_metadata = {"caption": "hi"}
    r = client.get("/api/images/current_metadata")
    assert r.status_code == 200
    assert r.get_json()["caption"] == "hi"


# ---------------------------------------------------------------------------
# GET /api/images/metadata
# ---------------------------------------------------------------------------


def test_get_image_metadata_missing_filename(client):
    r = client.get("/api/images/metadata")
    assert r.status_code == 400


def test_get_image_metadata_file_not_found(client):
    r = client.get("/api/images/metadata", query_string={"filename": "nope.jpg"})
    assert r.status_code == 404


def test_get_image_metadata_found(client, app, image_dir):
    (image_dir / "a.jpg").write_bytes(b"x")
    app.config["backend"].load_metadata_db.return_value = {
        "deadbeef": {"filename": "a.jpg", "caption": "hi"}
    }
    r = client.get("/api/images/metadata", query_string={"filename": "a.jpg"})
    assert r.status_code == 200
    assert r.get_json()["caption"] == "hi"


# ---------------------------------------------------------------------------
# POST /api/images/metadata
# ---------------------------------------------------------------------------


def test_update_metadata_unauth(unauth_client):
    r = unauth_client.post("/api/images/metadata", json={"hash": "x"})
    assert r.status_code == 401


def test_update_metadata_missing_hash(client):
    r = client.post("/api/images/metadata", json={})
    assert r.status_code == 400


def test_update_metadata_not_found(client, app):
    app.config["backend"].load_metadata_db.return_value = {}
    r = client.post("/api/images/metadata", json={"hash": "deadbeef"})
    assert r.status_code == 404


def test_update_metadata_ok(client, app, image_dir):
    (image_dir / "a.jpg").write_bytes(b"x")
    app.config["backend"].load_metadata_db.return_value = {
        "deadbeef": {"filename": "a.jpg", "caption": "old"}
    }
    r = client.post(
        "/api/images/metadata", json={"hash": "deadbeef", "caption": "new"}
    )
    assert r.status_code == 200
    app.config["backend"].save_metadata_db.assert_called_once()


def test_update_metadata_rename_conflict(client, app, image_dir):
    (image_dir / "a.jpg").write_bytes(b"x")
    (image_dir / "b.jpg").write_bytes(b"y")
    app.config["backend"].load_metadata_db.return_value = {
        "deadbeef": {"filename": "a.jpg", "caption": "old"}
    }
    r = client.post(
        "/api/images/metadata",
        json={"hash": "deadbeef", "caption": "new", "new_filename": "b.jpg"},
    )
    assert r.status_code == 409


# ---------------------------------------------------------------------------
# POST /api/images/upload
# ---------------------------------------------------------------------------


def test_upload_unauth(unauth_client):
    r = unauth_client.post("/api/images/upload", data={})
    assert r.status_code == 401


def test_upload_no_file(client):
    r = client.post("/api/images/upload", data={})
    assert r.status_code == 400


def test_upload_ok(client, app, image_dir):
    data = {"file": (io.BytesIO(b"fake image bytes"), "photo.jpg")}
    r = client.post(
        "/api/images/upload", data=data, content_type="multipart/form-data"
    )
    assert r.status_code == 200
    assert "photo.jpg" in r.get_json()["files"]
    assert (image_dir / "photo.jpg").exists()
    app.config["backend"].Frame.update_images_list.assert_called_once()


def test_upload_disallowed_extension_skipped(client, app):
    app.config["backend"].allowed_file.side_effect = lambda fn: False
    data = {"file": (io.BytesIO(b"nope"), "virus.exe")}
    r = client.post(
        "/api/images/upload", data=data, content_type="multipart/form-data"
    )
    assert r.status_code == 200
    assert r.get_json()["files"] == []


# ---------------------------------------------------------------------------
# GET /api/images/<filename> (serve) and DELETE
# ---------------------------------------------------------------------------


def test_serve_image_unauth(unauth_client):
    r = unauth_client.get("/api/images/a.jpg")
    assert r.status_code == 401


def test_serve_image_ok(client, image_dir):
    (image_dir / "a.jpg").write_bytes(b"x")
    r = client.get("/api/images/a.jpg")
    assert r.status_code == 200


def test_delete_image_unauth(unauth_client):
    r = unauth_client.delete("/api/images/a.jpg")
    assert r.status_code == 401


def test_delete_image_ok(client, app, image_dir):
    (image_dir / "a.jpg").write_bytes(b"x")
    r = client.delete("/api/images/a.jpg")
    assert r.status_code == 200
    assert not (image_dir / "a.jpg").exists()
    app.config["backend"].Frame.update_images_list.assert_called_once()


def test_delete_image_not_found(client):
    r = client.delete("/api/images/missing.jpg")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/images/thumb/<filename>
# ---------------------------------------------------------------------------


def test_thumb_unauth(unauth_client):
    r = unauth_client.get("/api/images/thumb/a.jpg")
    assert r.status_code == 401


def test_thumb_path_traversal_rejected(client, image_dir):
    r = client.get("/api/images/thumb/..%2F..%2Fetc%2Fpasswd")
    assert r.status_code == 404


def test_thumb_generates_and_serves(client, app, image_dir):
    (image_dir / "a.jpg").write_bytes(b"x")
    thumb_dir = image_dir / "_thumbs"
    thumb_dir.mkdir()
    dst = thumb_dir / "a_320.webp"

    def _make_thumb(src_path, dst_path, w):
        with open(dst_path, "wb") as f:
            f.write(b"webpdata")

    app.config["backend"]._thumb_path.return_value = str(dst)
    app.config["backend"]._make_thumb.side_effect = _make_thumb

    r = client.get("/api/images/thumb/a.jpg")
    assert r.status_code == 200
    assert r.headers["Content-Type"] == "image/webp"
