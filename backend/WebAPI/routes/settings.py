import queue
import threading

from flask import (
    Blueprint,
    Response,
    current_app,
    jsonify,
    request,
    stream_with_context,
)

from Utilities.config_store import SETTINGS_SCHEMA

settings_bp = Blueprint('settings_bp', __name__, url_prefix='/api/settings')

_sse_clients: list[queue.Queue] = []
_sse_lock = threading.Lock()


def _broadcast_settings_updated() -> None:
    with _sse_lock:
        clients = list(_sse_clients)
    for q in clients:
        try:
            q.put_nowait("settings_updated")
        except queue.Full:
            pass


def _register_sse_broadcaster() -> None:
    from Utilities.config_events import on_settings_changed
    on_settings_changed(lambda _: _broadcast_settings_updated())


_register_sse_broadcaster()

@settings_bp.route("/", methods=["GET"], strict_slashes=False)
def get_settings():
    backend = current_app.config.get('backend')
    if backend is None:
        return jsonify({"error": "backend unavailable"}), 500
    if not backend.is_authenticated():
        return jsonify({"error": "unauthorized"}), 401
    try:
        data = backend.load_settings()
        if not isinstance(data, dict):
            data = {}
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": "Failed to read settings", "details": str(e)}), 500

@settings_bp.route("/schema", methods=["GET"], strict_slashes=False)
def get_schema():
    backend = current_app.config.get("backend")
    if backend and not backend.is_authenticated():
        return jsonify({"error": "unauthorized"}), 401
    return jsonify(SETTINGS_SCHEMA)


@settings_bp.route("/", methods=["POST"], strict_slashes=False)
def update_settings():
    backend = current_app.config.get('backend')
    if backend is None:
        return jsonify({"error": "backend unavailable"}), 500
    if not backend.is_authenticated():
        return jsonify({"error": "unauthorized"}), 401
    
    try:
        new_settings = request.get_json(silent=True)
        if not isinstance(new_settings, dict):
            return jsonify({"error": "Invalid payload."}), 400
            
        backend.save_settings(new_settings)
        return jsonify({"message": "Settings updated successfully."})
    except Exception as e:
        return jsonify({"error": f"Failed to update settings: {e}"}), 500

@settings_bp.route("/events", methods=["GET"])
def settings_events():
    """Server-Sent Events stream. Pushes 'settings_updated' when settings change."""
    backend = current_app.config.get('backend')
    if backend and not backend.is_authenticated():
        return jsonify({"error": "unauthorized"}), 401

    def event_stream():
        q: queue.Queue = queue.Queue(maxsize=10)
        with _sse_lock:
            _sse_clients.append(q)
        try:
            yield "data: connected\n\n"
            while True:
                try:
                    msg = q.get(timeout=30)
                    yield f"data: {msg}\n\n"
                except queue.Empty:
                    yield ": keepalive\n\n"
        finally:
            with _sse_lock:
                try:
                    _sse_clients.remove(q)
                except ValueError:
                    pass

    return Response(
        stream_with_context(event_stream()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@settings_bp.route("/system_stats", methods=["GET"])
def system_stats():
    backend = current_app.config['backend']
    if not backend.is_authenticated():
        return jsonify({"error": "unauthorized"}), 401

    import psutil
    try:
        cpu_usage = int(psutil.cpu_percent(interval=None))
        ram = psutil.virtual_memory()
        ram_used = ram.used // (1024 * 1024)
        ram_total = ram.total // (1024 * 1024)
        ram_percent = ram.percent
        try:
            cpu_temps = psutil.sensors_temperatures().get("cpu_thermal", [])
            cpu_temp = round(cpu_temps[0].current, 1) if cpu_temps else "N/A"
        except Exception:
            cpu_temp = "N/A"

        return jsonify({
            "cpu_usage": cpu_usage,
            "ram_percent": ram_percent,
            "ram_used": ram_used,
            "ram_total": ram_total,
            "cpu_temp": cpu_temp
        })
    except Exception as e:
        return jsonify({"error": f"Stats unavailable: {e}"}), 500

@settings_bp.route("/logs", methods=["GET"])
def get_logs():
    backend = current_app.config['backend']
    if not backend.is_authenticated():
        return jsonify({"error": "unauthorized"}), 401
    try:
        with open(backend.LOG_FILE_PATH, "r") as log_file:
            logs = log_file.readlines()
        return jsonify({"logs": logs}), 200
    except FileNotFoundError:
        return jsonify({"error": "Log file not found"}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@settings_bp.route("/clear_logs", methods=["POST"])
def clear_logs():
    backend = current_app.config['backend']
    if not backend.is_authenticated():
        return jsonify({"error": "unauthorized"}), 401

    try:
        with open(backend.LOG_FILE_PATH, "w") as log_file:
            log_file.truncate(0)
        return jsonify({"message": "Log file cleared successfully."}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500
