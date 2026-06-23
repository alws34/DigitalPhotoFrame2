"""Tests for /api/stream endpoints — the primary regression guard for the MJPEG stream.

Critical regression: wlr-randr --off sends a QUIT event to the pygame window, which
causes srv.stop_services() to set is_running=False, making mjpeg_stream() exit
immediately so clients disconnect at once.  These tests catch that class of breakage.
"""

import pytest
from flask import Flask

from WebAPI.routes.stream import stream_bp

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_FAKE_JPEG = (
    b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    + b"\xff\xd9"
)


def _mjpeg_frame(data: bytes) -> bytes:
    return (
        b"--frame\r\n"
        + b"Content-Type: image/jpeg\r\n"
        + f"Content-Length: {len(data)}\r\n\r\n".encode()
        + data
        + b"\r\n"
    )


class _FakeBackend:
    def __init__(self, last_jpeg: bytes = _FAKE_JPEG, is_running: bool = True):
        self._last_jpeg = last_jpeg
        self._is_running = is_running

    def is_authenticated(self):
        return True

    def mjpeg_stream(self, w, h):
        if not self._is_running:
            return
        yield _mjpeg_frame(self._last_jpeg)


@pytest.fixture()
def app():
    a = Flask(__name__)
    a.testing = True
    a.secret_key = "test"
    a.config["backend"] = _FakeBackend()
    a.register_blueprint(stream_bp)
    return a


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def app_empty():
    """Backend with no current frame (startup state)."""
    a = Flask(__name__)
    a.testing = True
    a.secret_key = "test"
    a.config["backend"] = _FakeBackend(last_jpeg=b"")
    a.register_blueprint(stream_bp)
    return a


# ---------------------------------------------------------------------------
# /api/stream/snapshot
# ---------------------------------------------------------------------------


def test_snapshot_returns_200_with_jpeg(client):
    r = client.get("/api/stream/snapshot")
    assert r.status_code == 200
    assert r.content_type == "image/jpeg"
    assert r.data == _FAKE_JPEG


def test_snapshot_no_cache_headers(client):
    r = client.get("/api/stream/snapshot")
    cc = r.headers.get("Cache-Control", "")
    assert "no-cache" in cc or "no-store" in cc


def test_snapshot_503_when_no_frame(app_empty):
    r = app_empty.test_client().get("/api/stream/snapshot")
    assert r.status_code == 503


# ---------------------------------------------------------------------------
# /api/stream  (MJPEG)
# ---------------------------------------------------------------------------


def test_stream_200_multipart_mime(client):
    r = client.get("/api/stream")
    assert r.status_code == 200
    assert "multipart/x-mixed-replace" in r.content_type
    assert "frame" in r.content_type


def test_stream_contains_frame_boundary(client):
    r = client.get("/api/stream")
    assert b"--frame" in r.data


def test_stream_contains_jpeg_content_type(client):
    r = client.get("/api/stream")
    assert b"Content-Type: image/jpeg" in r.data


def test_stream_contains_actual_jpeg_bytes(client):
    r = client.get("/api/stream")
    assert _FAKE_JPEG in r.data


def test_stream_exits_cleanly_when_not_running():
    """Regression: if is_running=False, generator must exit (not loop forever)."""
    a = Flask(__name__)
    a.testing = True
    a.secret_key = "test"
    a.config["backend"] = _FakeBackend(is_running=False)
    a.register_blueprint(stream_bp)
    r = a.test_client().get("/api/stream")
    assert r.status_code == 200
    # No frames yielded, so body is empty beyond the wrapper
    assert b"--frame" not in r.data


# ---------------------------------------------------------------------------
# /api/stream/test
# ---------------------------------------------------------------------------


def test_stream_test_endpoint(client):
    """The /test endpoint should produce a colour-bar MJPEG (no real frame needed)."""
    r = client.get("/api/stream/test")
    assert r.status_code == 200
    assert "multipart/x-mixed-replace" in r.content_type
