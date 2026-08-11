import os

from flask import Blueprint, Response, current_app, jsonify, request

profile_bp = Blueprint("profile_bp", __name__, url_prefix="/api/profile")

# Profile picture lives in the data volume so it persists across container rebuilds
_DEFAULT_PROFILE_PATH = "/data/profile.png"


def _profile_path() -> str:
    return os.environ.get("PF_PROFILE_PATH", _DEFAULT_PROFILE_PATH)


@profile_bp.route("/picture", methods=["GET"])
def get_picture():
    """Serve profile picture. Public (no auth) so login screen can fetch it."""
    path = _profile_path()
    if not os.path.exists(path):
        return Response(status=404)
    with open(path, "rb") as f:
        data = f.read()
    return Response(
        data,
        mimetype="image/png",
        headers={"Cache-Control": "public, max-age=3600", "Content-Length": str(len(data))},
    )


@profile_bp.route("/picture", methods=["POST"])
def upload_picture():
    """Upload a pre-cropped circular PNG from the client-side crop editor."""
    backend = current_app.config["backend"]
    if not backend.is_authenticated():
        return jsonify({"error": "Unauthorized"}), 401

    file = request.files.get("file")
    if not file or not file.filename:
        return jsonify({"error": "No file provided"}), 400

    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in (".png", ".jpg", ".jpeg", ".webp"):
        return jsonify({"error": "Unsupported file type"}), 400

    path = _profile_path()
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        file.save(path)
        return jsonify({"success": True, "url": "/api/profile/picture"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@profile_bp.route("/picture", methods=["DELETE"])
def delete_picture():
    """Remove the profile picture."""
    backend = current_app.config["backend"]
    if not backend.is_authenticated():
        return jsonify({"error": "Unauthorized"}), 401

    path = _profile_path()
    if os.path.exists(path):
        try:
            os.remove(path)
        except OSError as e:
            return jsonify({"error": str(e)}), 500
    return jsonify({"success": True})
