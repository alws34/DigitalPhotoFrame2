"""On-device kiosk auto-login: pygame's triple-tap mints a one-time token
in-process (no HTTP surface — pygame and Flask share this process), hands it
to a locally-spawned webview subprocess, which exchanges it here for a real
session. Not an admin bypass: the token is single-use, short-lived, and only
ever handed to a process this same container spawned.
"""
import logging
import secrets
import time

from flask import Blueprint, current_app, redirect, request

from WebAPI.database import get_device_owner

kiosk_bp = Blueprint("kiosk_bp", __name__, url_prefix="/api/kiosk")

_TOKEN_TTL_SECONDS = 120  # generous: WebKit2 cold-start on Pi hardware can take a while
_tokens: dict[str, float] = {}


def mint_kiosk_token() -> str:
    """Create a single-use, short-lived token. Call in-process only."""
    now = time.monotonic()
    for t, expires in list(_tokens.items()):
        if expires < now:
            del _tokens[t]
    token = secrets.token_urlsafe(32)
    _tokens[token] = now + _TOKEN_TTL_SECONDS
    return token


@kiosk_bp.route("/login", methods=["GET"], strict_slashes=False)
def kiosk_login():
    if request.remote_addr not in ("127.0.0.1", "::1"):
        logging.warning("[Kiosk] Login hit from non-loopback address %s.", request.remote_addr)
        return "Forbidden", 403

    token = request.args.get("token", "")
    expires = _tokens.pop(token, None)
    if not token:
        logging.warning("[Kiosk] Login hit with no token.")
    elif expires is None:
        logging.warning("[Kiosk] Login token unknown (already used or never minted).")
    elif expires < time.monotonic():
        logging.warning("[Kiosk] Login token expired.")
    else:
        owner = get_device_owner()
        if owner:
            backend = current_app.config.get("backend")
            backend._rotate_session(owner["username"], owner["uid"], owner.get("role", "user"))
            logging.info("[Kiosk] Session rotated for device owner %s.", owner["username"])
        else:
            logging.warning("[Kiosk] Login token valid but no device owner account exists.")
    return redirect("/settings")
