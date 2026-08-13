import os
from datetime import datetime
from pathlib import Path

from flask import (
    Blueprint,
    current_app,
    jsonify,
    request,
    send_file,
    send_from_directory,
)

images_bp = Blueprint('images_bp', __name__, url_prefix='/api/images')

@images_bp.route("/", methods=["GET"])
def list_images():
    backend = current_app.config['backend']
    if not backend.is_authenticated():
        return jsonify({"error": "unauthorized"}), 401

    images = backend.get_images_from_directory()
    metadata_db = backend.load_metadata_db()

    def _date_for_filename(fn: str) -> str:
        for meta in metadata_db.values():
            if meta.get("filename") == fn and meta.get("date_added"):
                return meta["date_added"]
        fp = os.path.join(backend.IMAGE_DIR, fn)
        try:
            ts = os.path.getmtime(fp)
            return datetime.fromtimestamp(ts).isoformat()
        except Exception:
            return ""

    images_data = [{"name": fn, "date_added": _date_for_filename(fn)} for fn in images]
    return jsonify(images_data)

@images_bp.route("/current_metadata", methods=["GET"])
def current_metadata():
    backend = current_app.config['backend']
    with backend._metadata_lock:
        return jsonify(backend.latest_metadata or {})

@images_bp.route("/metadata", methods=["GET"])
def get_image_metadata():
    backend = current_app.config['backend']
    filename = request.args.get("filename")
    if not filename:
        return jsonify({"error": "Filename not provided."}), 400

    filepath = os.path.join(backend.IMAGE_DIR, filename)
    if not os.path.exists(filepath):
        return jsonify({"error": "File not found."}), 404

    metadata_db = backend.load_metadata_db()

    for meta in metadata_db.values():
        if meta.get("filename") == filename:
            return jsonify(meta)

    backend.store_image_metadata(filepath)
    metadata_db = backend.load_metadata_db()
    file_hash = backend.compute_image_hash(filepath)
    return jsonify(metadata_db.get(file_hash, {}))

@images_bp.route("/metadata", methods=["POST"])
def update_metadata():
    backend = current_app.config['backend']
    if not backend.is_authenticated():
        return jsonify({"error": "unauthorized"}), 401

    data = request.json or {}
    file_hash = data.get("hash")
    caption = data.get("caption", "")
    uploader = data.get("uploader")
    location = data.get("location")
    new_filename = data.get("new_filename")

    if not file_hash:
        return jsonify({"error": "Hash not provided."}), 400

    metadata_db = backend.load_metadata_db()

    if file_hash not in metadata_db:
        return jsonify({"error": "Metadata not found for this hash."}), 404

    entry = metadata_db[file_hash]
    entry["caption"] = caption
    if uploader is not None:
        entry["uploader"] = uploader
    if location is not None:
        entry["location"] = location

    if new_filename and new_filename != entry.get("filename"):
        old_path = os.path.join(backend.IMAGE_DIR, entry["filename"])
        new_path = os.path.join(backend.IMAGE_DIR, new_filename)
        if os.path.exists(new_path):
            return jsonify({"error": "A file with that name already exists."}), 409
        try:
            os.rename(old_path, new_path)
            entry["filename"] = new_filename
        except OSError as e:
            return jsonify({"error": f"Rename failed: {e}"}), 500

    backend.save_metadata_db({file_hash: entry})
    backend.latest_metadata = entry

    return jsonify({"message": "Metadata updated successfully.", "filename": entry["filename"]})

@images_bp.route("/upload", methods=["POST"])
def upload_files():
    backend = current_app.config['backend']
    if not backend.is_authenticated():
        return jsonify({"error": "unauthorized"}), 401

    if "file" not in request.files and "file[]" not in request.files:
        return jsonify({"error": "No file parameter"}), 400

    files = request.files.getlist("file[]") or request.files.getlist("file")
    uploaded_files_res = []

    for idx, file in enumerate(files):
        if file and backend.allowed_file(file.filename):
            original_filename = file.filename
            file_path = os.path.join(backend.IMAGE_DIR, original_filename)
            file_extension = Path(original_filename).suffix.lower()
            file.save(file_path)
            
            if file_extension in {".heic", ".heif"}:
                png_path = os.path.splitext(file_path)[0] + ".png"
                file_path = backend.convert_heic_to_png(file_path, png_path)
                original_filename = os.path.basename(file_path)

            caption = request.form.get(f"caption_{idx}", "").strip()
            uploader = request.form.get(f"uploader_{idx}", "").strip()

            file_hash = backend.compute_image_hash(file_path)

            metadata = {
                "hash": file_hash,
                "caption": caption,
                "uploader": uploader,
                "date_added": datetime.utcnow().isoformat(),
                "filename": original_filename,
            }
            backend.save_metadata_db({file_hash: metadata})
            uploaded_files_res.append(original_filename)

    backend.Frame.update_images_list()
    return jsonify({"message": "Upload successful", "files": uploaded_files_res}), 200

@images_bp.route("/<path:filename>", methods=["GET"])
def serve_image(filename):
    backend = current_app.config['backend']
    if not backend.is_authenticated():
        return jsonify({"error": "unauthorized"}), 401
    return send_from_directory(backend.IMAGE_DIR, filename)

@images_bp.route("/<path:filename>", methods=["DELETE"])
def delete_image(filename):
    backend = current_app.config['backend']
    if not backend.is_authenticated():
        return jsonify({"error": "unauthorized"}), 401

    try:
        os.remove(os.path.join(backend.IMAGE_DIR, filename))
        backend._purge_thumbs(filename)
        backend.Frame.update_images_list()
        return jsonify({"message": f"File {filename} successfully deleted."})
    except FileNotFoundError:
        return jsonify({"error": f"File {filename} not found."}), 404

@images_bp.route("/thumb/<path:filename>")
def thumb(filename):
    backend = current_app.config['backend']
    if not backend.is_authenticated():
        return jsonify({"error": "unauthorized"}), 401

    src_path = os.path.join(backend.IMAGE_DIR, filename)
    root_real = os.path.realpath(backend.IMAGE_DIR)
    path_real = os.path.realpath(src_path)
    if not (os.path.isfile(src_path) and os.path.commonpath([root_real, path_real]) == root_real):
        return jsonify({"error": "File not found"}), 404

    try:
        w = int(request.args.get("w", 320))
        w = max(64, min(w, 1920))
    except Exception:
        w = 320

    dst_path = backend._thumb_path(filename, w)

    try:
        if (not os.path.exists(dst_path)) or (
            os.path.getmtime(dst_path) < os.path.getmtime(src_path)
        ):
            backend._make_thumb(src_path, dst_path, w)
    except Exception:
        return send_from_directory(backend.IMAGE_DIR, filename)

    resp = send_file(dst_path, mimetype="image/webp", conditional=True)
    resp.headers["Cache-Control"] = "public, max-age=2592000, immutable"
    return resp
