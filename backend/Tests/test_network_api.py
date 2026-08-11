"""Tests for /api/network endpoints: wifi scan/connect."""
from unittest.mock import MagicMock, patch

import pytest
from flask import Flask

from WebAPI.routes.network import network_bp


@pytest.fixture()
def app():
    a = Flask(__name__)
    a.testing = True
    a.secret_key = "test"
    backend = MagicMock()
    backend.is_authenticated.return_value = True
    a.config["backend"] = backend
    a.register_blueprint(network_bp)
    return a


@pytest.fixture()
def client(app):
    return app.test_client()


NMCLI_OUTPUT = "MyHomeWifi:80:WPA2\nOpenGuest:60:--\nMyHomeWifi:55:WPA2\n"


def test_wifi_scan_unauth(app):
    app.config["backend"].is_authenticated.return_value = False
    r = app.test_client().post("/api/network/wifi/scan")
    assert r.status_code == 401


def test_wifi_scan_via_nmcli(client):
    with patch("WebAPI.routes.network.shutil.which", return_value="/usr/bin/nmcli"), \
         patch("WebAPI.routes.network.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=NMCLI_OUTPUT, stderr="")
        r = client.post("/api/network/wifi/scan")
    assert r.status_code == 200
    body = r.get_json()
    # Duplicate SSID collapsed, sorted by signal desc, open network has no security.
    assert body["networks"] == [
        {"ssid": "MyHomeWifi", "signal": 80, "security": "WPA2"},
        {"ssid": "OpenGuest", "signal": 60, "security": ""},
    ]


def test_wifi_scan_falls_back_to_iwlist_when_nmcli_missing(client):
    with patch("WebAPI.routes.network.shutil.which", side_effect=lambda x: None if x == "nmcli" else "/sbin/iwlist"), \
         patch("WebAPI.routes.network._scan_via_iwlist", return_value=[{"ssid": "Fallback", "signal": 40, "security": ""}]):
        r = client.post("/api/network/wifi/scan")
    assert r.status_code == 200
    assert r.get_json()["networks"][0]["ssid"] == "Fallback"


def test_wifi_scan_all_methods_fail(client):
    with patch("WebAPI.routes.network.shutil.which", return_value=None):
        r = client.post("/api/network/wifi/scan")
    assert r.status_code == 502
    assert r.get_json()["networks"] == []


def test_wifi_connect_requires_ssid(client):
    r = client.post("/api/network/wifi/connect", json={})
    assert r.status_code == 400


def test_wifi_connect_success(client):
    with patch("WebAPI.routes.network.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        r = client.post("/api/network/wifi/connect", json={"ssid": "MyHomeWifi", "password": "secret"})
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True
    args = mock_run.call_args[0][0]
    assert args == ["nmcli", "device", "wifi", "connect", "MyHomeWifi", "password", "secret"]


def test_wifi_connect_failure(client):
    with patch("WebAPI.routes.network.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="bad password")
        r = client.post("/api/network/wifi/connect", json={"ssid": "MyHomeWifi", "password": "wrong"})
    assert r.status_code == 502
    assert r.get_json()["ok"] is False
    assert "bad password" in r.get_json()["message"]


def test_wifi_connect_open_network_no_password_arg(client):
    with patch("WebAPI.routes.network.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        client.post("/api/network/wifi/connect", json={"ssid": "OpenGuest"})
    args = mock_run.call_args[0][0]
    assert args == ["nmcli", "device", "wifi", "connect", "OpenGuest"]


def test_wifi_connect_unauth(app):
    app.config["backend"].is_authenticated.return_value = False
    r = app.test_client().post("/api/network/wifi/connect", json={"ssid": "x"})
    assert r.status_code == 401
