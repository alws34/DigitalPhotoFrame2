import logging
import re
import shutil
import subprocess

from flask import Blueprint, current_app, jsonify, request

network_bp = Blueprint("network_bp", __name__, url_prefix="/api/network")


def _require_auth():
    backend = current_app.config.get("backend")
    if backend and not backend.is_authenticated():
        return jsonify({"error": "unauthorized"}), 401
    return None


def _scan_via_nmcli() -> list:
    """Scan using nmcli (requires D-Bus access to host NetworkManager)."""
    result = subprocess.run(
        ["nmcli", "--terse", "--escape", "no",
         "-f", "SSID,SIGNAL,SECURITY",
         "device", "wifi", "list", "--rescan", "yes"],
        capture_output=True, text=True, timeout=25,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "nmcli error")
    networks: list = []
    seen: set = set()
    for line in result.stdout.strip().splitlines():
        # Split from right: SSID can contain colons; SIGNAL and SECURITY cannot.
        parts = line.rsplit(":", 2)
        if len(parts) < 3:
            continue
        ssid, signal_str, security = parts[0].strip(), parts[1].strip(), parts[2].strip()
        if not ssid or ssid in seen:
            continue
        seen.add(ssid)
        try:
            signal = int(signal_str)
        except ValueError:
            signal = 0
        # nmcli uses "--" for open networks
        secured = bool(security and security != "--")
        networks.append({"ssid": ssid, "signal": signal, "security": security if secured else ""})
    return sorted(networks, key=lambda n: -n["signal"])


def _scan_via_iwlist() -> list:
    """Fallback scan using iwlist (wireless-tools, no daemon needed)."""
    iw_dev = subprocess.run(["iwconfig"], capture_output=True, text=True, timeout=5)
    ifaces = re.findall(r'^(\w+)\s+IEEE 802\.11', iw_dev.stdout, re.MULTILINE)
    if not ifaces:
        try:
            with open("/proc/net/wireless") as f:
                for line in f:
                    m = re.match(r'^\s*(\w+):', line)
                    if m:
                        ifaces.append(m.group(1))
        except OSError:
            pass
    if not ifaces:
        raise RuntimeError("No wireless interface found")

    iface = ifaces[0]
    result = subprocess.run(
        ["iwlist", iface, "scan"],
        capture_output=True, text=True, timeout=20,
    )
    networks: list = []
    seen: set = set()
    current: dict = {}
    for line in result.stdout.splitlines():
        line = line.strip()
        m = re.search(r'ESSID:"(.*?)"', line)
        if m:
            ssid = m.group(1)
            if ssid and ssid not in seen:
                if current.get("ssid"):
                    networks.append(current)
                seen.add(ssid)
                current = {"ssid": ssid, "signal": 0, "security": ""}
        m = re.search(r'Quality=(\d+)/(\d+)', line)
        if m and current:
            current["signal"] = int(int(m.group(1)) * 100 / int(m.group(2)))
        if "Encryption key:on" in line and current:
            current["security"] = "WPA"
    if current.get("ssid"):
        networks.append(current)
    return sorted(networks, key=lambda n: -n["signal"])


@network_bp.route("/wifi/scan", methods=["POST"], strict_slashes=False)
def wifi_scan():
    unauth = _require_auth()
    if unauth:
        return unauth

    errors = []
    networks: list = []
    try:
        if shutil.which("nmcli"):
            networks = _scan_via_nmcli()
        else:
            errors.append("nmcli not found")
    except Exception as exc:
        logging.warning("nmcli scan failed: %s", exc)
        errors.append(f"nmcli: {exc}")

    if not networks:
        try:
            if shutil.which("iwlist"):
                networks = _scan_via_iwlist()
            else:
                errors.append("iwlist not found")
        except Exception as exc:
            logging.warning("iwlist scan failed: %s", exc)
            errors.append(f"iwlist: {exc}")

    if networks:
        return jsonify({"networks": networks})
    return jsonify({"networks": [], "error": "; ".join(errors) or "No networks found"}), 502


@network_bp.route("/wifi/connect", methods=["POST"], strict_slashes=False)
def wifi_connect():
    unauth = _require_auth()
    if unauth:
        return unauth

    data = request.get_json(silent=True) or {}
    ssid = (data.get("ssid") or "").strip()
    password = data.get("password") or ""
    if not ssid:
        return jsonify({"ok": False, "message": "ssid required"}), 400

    try:
        cmd = ["nmcli", "device", "wifi", "connect", ssid]
        if password:
            cmd += ["password", password]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            return jsonify({"ok": True, "message": f"Connected to {ssid}"})
        err = (result.stderr or result.stdout).strip()
        return jsonify({"ok": False, "message": err[:200] if err else "Connection failed"}), 502
    except Exception as exc:
        return jsonify({"ok": False, "message": str(exc)}), 500
