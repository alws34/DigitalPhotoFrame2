"""Flask routes for /api/sources — source management and OAuth."""
from flask import Blueprint, current_app, jsonify, request

sources_bp = Blueprint("sources_bp", __name__, url_prefix="/api/sources")


def _get_album_manager():
    backend = current_app.config["backend"]
    am = getattr(backend, "album_manager", None)
    return am


@sources_bp.route("/", methods=["GET"], strict_slashes=False)
def list_sources():
    backend = current_app.config["backend"]
    if not backend.is_authenticated():
        return jsonify({"error": "unauthorized"}), 401

    am = _get_album_manager()
    if am is None:
        return jsonify({"error": "AlbumManager not available"}), 503

    try:
        sources = am.get_sources()
        return jsonify(sources)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@sources_bp.route("/", methods=["POST"], strict_slashes=False)
def add_source():
    backend = current_app.config["backend"]
    if not backend.is_authenticated():
        return jsonify({"error": "unauthorized"}), 401

    am = _get_album_manager()
    if am is None:
        return jsonify({"error": "AlbumManager not available"}), 503

    data = request.get_json(silent=True) or {}
    source_type = data.get("type", "")
    name = data.get("name", "")
    config = data.get("config", {})
    credentials = data.get("credentials", {})

    try:
        source_id = am.add_source(source_type, name, config, credentials)
        sources = am.get_sources()
        created = next((s for s in sources if s["id"] == source_id), {"id": source_id})
        return jsonify(created), 201
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@sources_bp.route("/<source_id>", methods=["DELETE"])
def remove_source(source_id):
    backend = current_app.config["backend"]
    if not backend.is_authenticated():
        return jsonify({"error": "unauthorized"}), 401

    am = _get_album_manager()
    if am is None:
        return jsonify({"error": "AlbumManager not available"}), 503

    try:
        am.remove_source(source_id)
        return "", 204
    except KeyError:
        return jsonify({"error": "Source not found"}), 404
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@sources_bp.route("/<source_id>/sync", methods=["POST"])
def trigger_sync(source_id):
    backend = current_app.config["backend"]
    if not backend.is_authenticated():
        return jsonify({"error": "unauthorized"}), 401

    am = _get_album_manager()
    if am is None:
        return jsonify({"error": "AlbumManager not available"}), 503

    try:
        am.trigger_sync(source_id)
        return jsonify({"status": "queued"}), 202
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@sources_bp.route("/<source_id>/remote-albums", methods=["GET"])
def list_remote_albums(source_id):
    backend = current_app.config["backend"]
    if not backend.is_authenticated():
        return jsonify({"error": "unauthorized"}), 401

    am = _get_album_manager()
    if am is None:
        return jsonify({"error": "AlbumManager not available"}), 503

    try:
        albums = am.list_remote_albums(source_id)
        return jsonify(
            [
                {
                    "remote_id": a.remote_id,
                    "name": a.name,
                    "media_count": a.media_count,
                }
                for a in albums
            ]
        )
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@sources_bp.route("/<source_id>/auth/start", methods=["POST"])
def auth_start(source_id):
    backend = current_app.config["backend"]
    if not backend.is_authenticated():
        return jsonify({"error": "unauthorized"}), 401

    if _get_album_manager() is None:
        return jsonify({"error": "AlbumManager not available"}), 503

    data = request.get_json(silent=True) or {}
    client_id = data.get("client_id", "")
    client_secret = data.get("client_secret", "")
    redirect_uri = data.get("redirect_uri", "")

    try:
        import json as _json

        from Utilities.sources.google_photos import GooglePhotosSource
        from WebAPI.database import get_db

        redirect_url = GooglePhotosSource.get_auth_url(client_id, redirect_uri)

        # Store client_id/client_secret temporarily in source config
        with get_db() as conn:
            row = conn.execute(
                "SELECT config_json FROM sources WHERE id = ?", (source_id,)
            ).fetchone()
            cfg = _json.loads(row["config_json"] or "{}") if row else {}
            cfg["client_id"] = client_id
            cfg["client_secret"] = client_secret
            if redirect_uri:
                cfg["redirect_uri"] = redirect_uri
            conn.execute(
                "UPDATE sources SET config_json = ? WHERE id = ?",
                (_json.dumps(cfg), source_id),
            )

        return jsonify({"redirect_url": redirect_url})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@sources_bp.route("/<source_id>/auth/callback", methods=["GET"])
def auth_callback(source_id):
    am = _get_album_manager()
    if am is None:
        return (
            "<html><body><p>AlbumManager not available.</p></body></html>",
            503,
        )

    code = request.args.get("code", "")

    try:
        import json as _json

        from Utilities.sources.google_photos import GooglePhotosSource
        from WebAPI.database import get_db

        with get_db() as conn:
            row = conn.execute(
                "SELECT config_json FROM sources WHERE id = ?", (source_id,)
            ).fetchone()
        cfg = _json.loads(row["config_json"] or "{}") if row else {}
        client_id = cfg.get("client_id", "")
        client_secret = cfg.get("client_secret", "")
        redirect_uri = cfg.get("redirect_uri", "")

        tokens = GooglePhotosSource.exchange_code(
            client_id, client_secret, code, redirect_uri
        )
        am.update_source_credentials(source_id, tokens)

        return (
            "<html><body>"
            "<script>window.close()</script>"
            "<p>Connected! You can close this tab.</p>"
            "</body></html>"
        )
    except Exception as exc:
        return (
            f"<html><body><p>Authentication failed: {exc}</p></body></html>",
            500,
        )
