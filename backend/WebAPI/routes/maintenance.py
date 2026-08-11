import logging
import threading
import time

from flask import Blueprint, current_app, jsonify

maintenance_bp = Blueprint("maintenance_bp", __name__, url_prefix="/api/maintenance")


@maintenance_bp.route("/restart", methods=["POST"])
def restart_service():
    backend = current_app.config.get("backend")
    if backend and not backend.is_authenticated():
        return jsonify({"error": "unauthorized"}), 401

    restart_fn = current_app.config.get("restart_fn")
    if restart_fn is None:
        return jsonify({"error": "restart not configured"}), 501

    def _do_restart():
        time.sleep(0.3)  # Allow HTTP response to reach client before restart
        try:
            restart_fn()
        except Exception as e:
            logging.error("[Maintenance] Restart failed: %s", e, exc_info=True)

    threading.Thread(target=_do_restart, daemon=True).start()
    return jsonify({"message": "Restarting…"}), 202
