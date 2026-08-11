import pytest
import requests

from Utilities.sources.immich import ImmichSource


class FakeResponse:
    def __init__(self, status_code=200, json_data=None, text=""):
        self.status_code = status_code
        self._json_data = json_data if json_data is not None else []
        self.text = text

    def json(self):
        return self._json_data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} error", response=self)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def iter_content(self, chunk_size=65536):
        yield b"jpeg"


def test_immich_authentication_tracks_validation_result(monkeypatch):
    def fake_get(*args, **kwargs):
        return FakeResponse(status_code=403, text="missing scope")

    monkeypatch.setattr(requests, "get", fake_get)
    source = ImmichSource()

    assert source.authenticate({"base_url": "http://immich", "api_key": "bad"}) is False
    assert source.is_authenticated is False


def test_immich_authentication_success_sets_authenticated(monkeypatch):
    def fake_get(*args, **kwargs):
        return FakeResponse(status_code=200)

    monkeypatch.setattr(requests, "get", fake_get)
    source = ImmichSource()

    assert source.authenticate({"base_url": "http://immich", "api_key": "ok"}) is True
    assert source.is_authenticated is True


def test_download_asset_logs_scope_hint_on_forbidden(monkeypatch, tmp_path, caplog):
    def fake_get(*args, **kwargs):
        return FakeResponse(status_code=403, text="forbidden")

    monkeypatch.setattr(requests, "get", fake_get)
    source = ImmichSource()
    source._base_url = "http://immich"
    source._api_key = "key"
    source._authenticated = True

    with pytest.raises(requests.HTTPError):
        source.download_asset("asset-1", tmp_path / "asset.jpg")

    assert "asset.view scope" in caplog.text
