"""Flask routes for /api/albums — album subscription and active album selection."""
from flask import Blueprint, current_app, jsonify, request

albums_bp = Blueprint("albums_bp", __name__, url_prefix="/api/albums")


def _get_album_manager():
    backend = current_app.config["backend"]
    am = getattr(backend, "album_manager", None)
    return am


def _active_response(am, album_id: str) -> dict:
    """Build the standard active-album response dict."""
    if album_id == "all" or not album_id:
        return {"album_id": "all", "name": "Local Images"}
    # Look up name from subscribed albums
    try:
        albums = am.get_albums()
        match = next((a for a in albums if a["id"] == album_id), None)
        name = match["name"] if match else album_id
    except Exception:
        name = album_id
    return {"album_id": album_id, "name": name}


# IMPORTANT: /active must be registered before /<album_id> to avoid Flask
# routing conflicts (literal path segment wins when registered first).
@albums_bp.route("/active", methods=["GET"])
def get_active_album():
    backend = current_app.config["backend"]
    if not backend.is_authenticated():
        return jsonify({"error": "unauthorized"}), 401

    am = _get_album_manager()
    if am is None:
        return jsonify({"error": "AlbumManager not available"}), 503

    try:
        album_id = am.get_active_album_id()
        return jsonify(_active_response(am, album_id))
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@albums_bp.route("/active", methods=["PUT"])
def set_active_album():
    backend = current_app.config["backend"]
    if not backend.is_authenticated():
        return jsonify({"error": "unauthorized"}), 401

    am = _get_album_manager()
    if am is None:
        return jsonify({"error": "AlbumManager not available"}), 503

    data = request.get_json(silent=True) or {}
    album_id = data.get("album_id", "all")

    try:
        am.set_active_album(album_id)
        return jsonify(_active_response(am, album_id))
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@albums_bp.route("/", methods=["GET"], strict_slashes=False)
def list_albums():
    backend = current_app.config["backend"]
    if not backend.is_authenticated():
        return jsonify({"error": "unauthorized"}), 401

    am = _get_album_manager()
    if am is None:
        return jsonify({"error": "AlbumManager not available"}), 503

    try:
        albums = am.get_albums()
        return jsonify(albums)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@albums_bp.route("/", methods=["POST"], strict_slashes=False)
def subscribe_album():
    backend = current_app.config["backend"]
    if not backend.is_authenticated():
        return jsonify({"error": "unauthorized"}), 401

    am = _get_album_manager()
    if am is None:
        return jsonify({"error": "AlbumManager not available"}), 503

    data = request.get_json(silent=True) or {}
    source_id = data.get("source_id", "")
    remote_id = data.get("remote_id", "")
    name = data.get("name", "")

    try:
        album_id = am.subscribe_album(source_id, remote_id, name)
        albums = am.get_albums()
        created = next((a for a in albums if a["id"] == album_id), {"id": album_id})
        return jsonify(created), 201
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@albums_bp.route("/<album_id>", methods=["DELETE"])
def unsubscribe_album(album_id):
    backend = current_app.config["backend"]
    if not backend.is_authenticated():
        return jsonify({"error": "unauthorized"}), 401

    am = _get_album_manager()
    if am is None:
        return jsonify({"error": "AlbumManager not available"}), 503

    try:
        am.unsubscribe_album(album_id)
        return "", 204
    except KeyError:
        return jsonify({"error": "Album not found"}), 404
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
