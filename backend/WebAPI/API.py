import hashlib
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from queue import Full, Queue
from threading import Event, Thread

import cv2
import numpy as np
from flask import (
    Flask,
    jsonify,
    request,
    send_from_directory,
    session,
)
from flask_cors import CORS
from numpy import ndarray
from PIL import Image, ImageOps
from tqdm import tqdm
from werkzeug.exceptions import HTTPException

from FrameServer.PhotoFrameServer import iFrame
from Utilities.config_store import load_settings as _cs_load
from Utilities.config_store import save_settings as _cs_save
from WebAPI.WebUtils.auth_security import (
    RateLimiter,
    UserStore,
    ensure_csrf,
    validate_csrf,
)

# ---------------------------------------------------------------------
# HEIC support
# ---------------------------------------------------------------------
has_pillow_heif = False

try:
    from pillow_heif import register_heif_opener
    register_heif_opener()
    has_pillow_heif = True
    print("[Backend] HEIF/HEIC plugin registered successfully (pillow-heif).")
except ImportError:
    print("[Backend] pillow-heif not installed. HEIC conversion will be unavailable.")
    has_pillow_heif = False
except Exception as e:
    print(f"[Backend] Could not register HEIF/HEIC plugin: {e}")
    has_pillow_heif = False


# ---------------------------------------------------------------------
# Helpers for nested form parsing
# ---------------------------------------------------------------------

def _parse_value(s: str):
    if isinstance(s, bool):
        return s
    if not isinstance(s, str):
        return s
    v = s.strip()
    if v.lower() in ("true", "on"):
        return True
    if v.lower() in ("false", "off"):
        return False
    try:
        if v.isdigit() or (v.startswith("-") and v[1:].isdigit()):
            return int(v)
    except Exception:
        pass
    try:
        return float(v)
    except Exception:
        return v


def _split_bracketed(key: str):
    parts = []
    i = 0
    while i < len(key):
        j = key.find("[", i)
        if j == -1:
            parts.append(key[i:])
            break
        parts.append(key[i:j])
        k = key.find("]", j + 1)
        parts.append(key[j + 1:k])
        i = k + 1
    return [p for p in parts if p != ""]


def _assign_path(root, parts, value):
    cur = root
    for idx, part in enumerate(parts):
        is_last = idx == len(parts) - 1
        is_int = False
        try:
            i_part = int(part)
            is_int = True
        except Exception:
            i_part = None

        if is_last:
            if is_int:
                if not isinstance(cur, list):
                    return
                while len(cur) <= i_part:
                    cur.append(None)
                cur[i_part] = value
            else:
                if isinstance(cur, list):
                    cur.append({part: value})
                else:
                    cur[part] = value
            return

        if is_int:
            if part == "" and isinstance(cur, list):
                nxt = {}
                cur.append(nxt)
                cur = nxt
                continue
            if not isinstance(cur, list):
                return
            while len(cur) <= i_part:
                cur.append({})
            if not isinstance(cur[i_part], (dict, list)):
                cur[i_part] = {}
            cur = cur[i_part]
        else:
            if not isinstance(cur, dict):
                return
            if part not in cur or not isinstance(cur[part], (dict, list)):
                nxt_is_int = False
                if idx + 1 < len(parts):
                    try:
                        _ = int(parts[idx + 1])
                        nxt_is_int = True
                    except Exception:
                        pass
                cur[part] = [] if nxt_is_int else {}
            cur = cur[part]


# ---------------------------------------------------------------------
# Backend
# ---------------------------------------------------------------------

class APIServer:
    def __init__(self, frame: iFrame, image_dir=None, settings_path=None):
        base = Path(__file__).parent
        self.app = Flask(
            __name__,
            static_folder=str(base.parent.parent / "frontend" / "dist"),
            static_url_path="/",
        )
        CORS(self.app, supports_credentials=True)

        env_secret = os.getenv("PHOTOFRAME_SECRET_KEY")
        self.app.config.update(
            SESSION_COOKIE_HTTPONLY=True,
            SESSION_COOKIE_SECURE=False,
            SESSION_COOKIE_SAMESITE="Lax",
            PERMANENT_SESSION_LIFETIME=3600,
            MAX_CONTENT_LENGTH=200 * 1024 * 1024,  # 200 MB per batch (5 × 40 MB photos)
        )

        self.METADATA_FILE = self.set_absolute_paths("metadata.json")
        self.LOG_FILE_PATH = self.set_absolute_paths("PhotoFrame.log")
        self.WEATHER_CACHE = self.set_absolute_paths("weather_cache.json")

        self._users = UserStore()

        self._rl_login = RateLimiter(limit=10, window_sec=60)
        self._rl_signup = RateLimiter(limit=5, window_sec=300)

        self.Frame = frame
        self.album_manager = None
        _init_settings = _cs_load()

        # --- Resolve IMAGE_DIR using 'system' or root ---
        sys_cfg = (_init_settings.get("system", {}) or {})
        img_cfg = (
            image_dir
            or sys_cfg.get("image_dir")
            or _init_settings.get("image_dir")
            or _init_settings.get("images_dir")
            or "Images"
        )
        self.IMAGE_DIR = self._resolve_dir(img_cfg)
        os.makedirs(self.IMAGE_DIR, exist_ok=True)
        print(f"[Backend] Using IMAGE_DIR = {self.IMAGE_DIR}")
        self.THUMB_DIR = os.path.join(os.path.dirname(self.IMAGE_DIR), "_thumbs")
        os.makedirs(self.THUMB_DIR, exist_ok=True)

        # --- Resolve log_file_path using 'system' or root ---
        try:
            cfg_log_path = sys_cfg.get("log_file_path") or _init_settings.get("log_file_path")
            
            if cfg_log_path:
                self.LOG_FILE_PATH = (
                    cfg_log_path
                    if os.path.isabs(cfg_log_path)
                    else self.set_absolute_paths(cfg_log_path)
                )
                log_dir = os.path.dirname(self.LOG_FILE_PATH)
                if log_dir and not os.path.exists(log_dir):
                    os.makedirs(log_dir, exist_ok=True)
                if not os.path.exists(self.LOG_FILE_PATH):
                    with open(self.LOG_FILE_PATH, "a", encoding="utf-8"):
                        pass
        except Exception as e:
            print(f"[Backend] Could not apply log_file_path override: {e}")

        backend_cfg = _init_settings.get("backend_configs") or {}
        self.app.secret_key = env_secret or backend_cfg.get("supersecretkey", "CHANGE_ME")
        self.stream_h = int(backend_cfg.get("stream_height", 1080))
        self.stream_w = int(backend_cfg.get("stream_width", 1920))
        self.port = int(backend_cfg.get("server_port", 80))
        self.host = str(backend_cfg.get("host", "0.0.0.0"))

        self.ALLOWED_EXTENSIONS = {
            ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tiff", ".webp",
            ".heic", ".heif", ".mov", ".mp4"
        }
        self.SELECTED_COLOR = "#ffcccc"

        self.latest_metadata = {}
        self._metadata_lock = threading.Lock()
        self._ensure_storage_files()
        
        # Initialize SQLite DB and migrate metadata.json if needed
        from WebAPI.database import init_db, migrate_jsons_if_needed
        init_db()
        migrate_jsons_if_needed(self.METADATA_FILE)

        self._normalize_existing_heic_images()

        # --- Encoding quality from 'system' or root ---
        self.encoding_quality = int(
            sys_cfg.get("image_quality_encoding")
            or _init_settings.get("image_quality_encoding")
            or 80
        )

        self._jpeg_queue = Queue(maxsize=30)
        self._new_frame_ev = Event()
        self._stop_event = Event()
        self._last_jpeg: bytes = b""

        self.executor = ThreadPoolExecutor(max_workers=2)
        self.setup_routes()
        Thread(target=self._capture_loop, daemon=True).start()

    # -----------------------------------------------------------------
    # Frame handling helpers
    # -----------------------------------------------------------------

    def _sanitize_frame(self, frame):
        """
        Normalize a frame to a uint8 BGR ndarray or return None if invalid.
        """
        if frame is None:
            return None

        if not isinstance(frame, np.ndarray):
            print(f"[Backend] _sanitize_frame: unexpected frame type: {type(frame)}")
            return None

        if frame.dtype == object:
            print(f"[Backend] _sanitize_frame: bad dtype=object, shape={frame.shape}")
            return None

        if frame.ndim == 2:
            frame = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
        elif frame.ndim == 3 and frame.shape[2] == 4:
            frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)

        if frame.dtype != np.uint8:
            try:
                frame = frame.astype(np.uint8)
            except Exception as e:
                print(f"[Backend] _sanitize_frame: cannot cast dtype {frame.dtype} to uint8: {e}")
                return None

        return frame

    def _client_ip(self) -> str:
        return (
            request.headers.get("X-Forwarded-For", request.remote_addr or "unknown")
            .split(",")[0]
            .strip()
        )

    def _rotate_session(self, username: str, uid: str, role: str) -> None:
        session.clear()
        session["uid"] = uid
        session["user"] = username
        session["role"] = role
        ensure_csrf(session)

    def _require_csrf(self) -> None:
        token = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token")
        if not validate_csrf(session, token):
            raise PermissionError("Invalid request")

    # -----------------------------------------------------------------
    # Paths and storage
    # -----------------------------------------------------------------

    def _resolve_dir(self, p: str) -> str:
        pth = Path(p).expanduser()
        if not pth.is_absolute():
            app_root = Path(__file__).resolve().parents[2]
            pth = app_root / pth
        return str(pth)

    def set_absolute_paths(self, path: str) -> str:
        if os.path.isabs(path):
            return os.path.abspath(path)
        base = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        return os.path.abspath(os.path.join(base, path))

    def _ensure_storage_files(self) -> None:
        os.makedirs(self.IMAGE_DIR, exist_ok=True)

        try:
            d = os.path.dirname(self.LOG_FILE_PATH)
            if d and not os.path.exists(d):
                os.makedirs(d, exist_ok=True)
            if not os.path.exists(self.LOG_FILE_PATH):
                with open(self.LOG_FILE_PATH, "w", encoding="utf-8"):
                    pass
        except Exception as e:
            print(f"[Backend] Could not initialize {self.LOG_FILE_PATH}: {e}")

    # -----------------------------------------------------------------
    # Capture loop
    # -----------------------------------------------------------------

    def _is_valid_frame(self, frame) -> bool:
        if not isinstance(frame, ndarray):
            return False
        if frame.dtype == object:
            print(f"[Backend] _capture_loop: got dtype=object, shape={frame.shape}, skipping")
            return False
        if frame.ndim not in (2, 3):
            print(f"[Backend] _capture_loop: unexpected ndim={frame.ndim}, shape={frame.shape}")
            return False
        return True

    def _capture_loop(self):
        idle_fps = float((_cs_load().get("backend_configs") or {}).get("idle_fps", 5)) or 5.0
        interval = 1.0 / max(0.1, idle_fps)

        last_jpeg = self._make_heartbeat_jpeg(self.stream_w, self.stream_h)
        next_deadline = time.perf_counter()

        print(f"[Backend] capture loop started (idle_fps={idle_fps})")

        while not self._stop_event.is_set() and self.Frame.get_is_running():
            try:
                timeout = max(0.0, next_deadline - time.perf_counter())
                got_new = self._new_frame_ev.wait(timeout=timeout)
                if got_new:
                    self._new_frame_ev.clear()
                    frame = self.Frame.get_stream_frame()

                    if self._is_valid_frame(frame):
                        if frame.ndim == 2:
                            frame = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
                        elif frame.ndim == 3 and frame.shape[2] == 4:
                            frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)

                        if frame.dtype != np.uint8:
                            try:
                                frame = frame.astype(np.uint8)
                            except Exception as e:
                                print(f"[Backend] cannot cast frame dtype {frame.dtype} to uint8: {e}")
                                frame = None

                        if frame is not None:
                            ok, jpg = cv2.imencode(
                                ".jpg",
                                frame,
                                [cv2.IMWRITE_JPEG_QUALITY, self.encoding_quality],
                            )
                            if ok:
                                last_jpeg = jpg.tobytes()

                now = time.perf_counter()
                if now >= next_deadline:
                    if last_jpeg is None:
                        last_jpeg = self._make_heartbeat_jpeg(self.stream_w, self.stream_h)

                    self._last_jpeg = last_jpeg

                    try:
                        self._jpeg_queue.put_nowait(last_jpeg)
                    except Full:
                        try:
                            _ = self._jpeg_queue.get_nowait()
                        except Exception:
                            pass
                        try:
                            self._jpeg_queue.put_nowait(last_jpeg)
                        except Exception:
                            pass

                    next_deadline = now + interval
            except Exception as e:
                print(f"[Backend] capture loop error: {e}")
                time.sleep(0.5)

    def _encode_and_queue(self, frame: ndarray):
        frame = self._sanitize_frame(frame)
        if frame is None:
            return
        success, jpg = cv2.imencode(
            ".jpg",
            frame,
            [cv2.IMWRITE_JPEG_QUALITY, self.encoding_quality],
        )
        if not success:
            return
        data = jpg.tobytes()
        try:
            self._jpeg_queue.put_nowait(data)
        except Full:
            _ = self._jpeg_queue.get_nowait()
            self._jpeg_queue.put_nowait(data)

    # -----------------------------------------------------------------
    # Settings / users / metadata
    # -----------------------------------------------------------------

    def load_settings(self):
        return _cs_load()

    def save_settings(self, data: dict):
        _cs_save(data)

    def allowed_file(self, filename):
        return "." in filename and Path(filename).suffix.lower() in self.ALLOWED_EXTENSIONS

    def get_images_from_directory(self):
        root = Path(self.IMAGE_DIR)
        return [
            str(entry.relative_to(root))
            for entry in root.rglob("*")
            if entry.is_file() and self.allowed_file(entry.name)
        ]

    def is_authenticated(self) -> bool:
        return "uid" in session and "user" in session

    def load_metadata_db(self):
        from WebAPI.database import get_all_metadata
        return get_all_metadata()

    def save_metadata_db(self, data):
        from WebAPI.database import delete_metadata, get_all_metadata, update_metadata
        current_data = get_all_metadata()
        for h, d in data.items():
            update_metadata(h, d)
        for h in current_data:
            if h not in data:
                delete_metadata(h)

    def compute_image_hash(self, image_path):
        hash_obj = hashlib.sha256()
        with open(image_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hash_obj.update(chunk)
        return hash_obj.hexdigest()

    # -----------------------------------------------------------------
    # Metadata update APIs (shared with older code)
    # -----------------------------------------------------------------

    def update_image_metadata(self, image_path):
        from WebAPI.database import get_metadata, update_metadata
        image_hash = self.compute_image_hash(image_path)
        existing = get_metadata(image_hash)
        
        if existing:
            entry = existing
        else:
            entry = {
                "hash": image_hash,
                "filename": os.path.basename(image_path),
                "uploader": "unknown",
                "date_added": datetime.now().isoformat(),
                "caption": "",
            }
            update_metadata(image_hash, entry)

        self.update_current_metadata(entry)

    def store_image_metadata(self, image_path):
        self.update_image_metadata(image_path)

    # -----------------------------------------------------------------
    # Streaming helpers
    # -----------------------------------------------------------------

    def mjpeg_stream(self, screen_w, screen_h):
        from queue import Empty
        boundary_line = b"--frame\r\n"
        while self.Frame.get_is_running():
            try:
                data = self._jpeg_queue.get(timeout=1.0)
            except Empty:
                continue
            yield (
                boundary_line
                + b"Content-Type: image/jpeg\r\n"
                + f"Content-Length: {len(data)}\r\n\r\n".encode("ascii")
                + data
                + b"\r\n"
            )

    def _thumb_path(self, filename: str, w: int) -> str:
        base, _ = os.path.splitext(filename)
        safe = base.replace(os.sep, "_")
        return os.path.join(self.THUMB_DIR, f"{safe}_w{w}.webp")

    def _make_thumb(self, src_path: str, dst_path: str, w: int) -> None:
        """
        Generates a thumbnail. Handles both Images (PIL) and Videos (OpenCV).
        """
        ext = os.path.splitext(src_path)[1].lower()
        
        # 1. Handle Videos
        if ext in (".mov", ".mp4"):
            self._make_video_thumb(src_path, dst_path, w)
            return

        # 2. Handle Images
        try:
            with Image.open(src_path) as im:
                im = ImageOps.exif_transpose(im)
                ratio = w / float(im.width)
                h = max(1, int(im.height * ratio))
                im = im.resize((w, h), Image.Resampling.LANCZOS)
                
                os.makedirs(os.path.dirname(dst_path), exist_ok=True)
                im.save(dst_path, "WEBP", quality=75, method=6)
        except Exception as e:
            print(f"[Backend] Image thumb generation failed for {src_path}: {e}")

    def _make_video_thumb(self, src_path: str, dst_path: str, w: int) -> None:
        try:
            cap = cv2.VideoCapture(src_path)
            if not cap.isOpened():
                return
            
            ret, frame = cap.read()
            cap.release()
            
            if ret and frame is not None:
                h_orig, w_orig = frame.shape[:2]
                ratio = w / float(w_orig)
                h = max(1, int(h_orig * ratio))
                
                frame = cv2.resize(frame, (w, h), interpolation=cv2.INTER_AREA)
                os.makedirs(os.path.dirname(dst_path), exist_ok=True)
                cv2.imwrite(dst_path, frame, [cv2.IMWRITE_WEBP_QUALITY, 75])
        except Exception as e:
            print(f"[Backend] Video thumb generation failed for {src_path}: {e}")
            
    def _make_heartbeat_jpeg(self, w, h):
        try:
            img = np.zeros(
                (max(120, min(h, 360)), max(160, min(w, 640)), 3),
                dtype="uint8",
            )
            img[:] = (32, 32, 32)
            cv2.putText(
                img,
                "Waiting for frames...",
                (10, 50),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.9,
                (200, 200, 200),
                2,
                cv2.LINE_AA,
            )
            ok, jpg = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 80])
            if ok:
                return jpg.tobytes()
        except Exception as e:
            print(f"[Backend] heartbeat build failed: {e}")
        return (
            b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
            b"\xff\xdb\x00C\x00"
            + b"\x08" * 64
            + b"\xff\xc0\x00\x11\x08\x00\x10\x00\x10\x03\x01\x22\x00\x02\x11\x01\x03\x11\x01"
            b"\xff\xc4\x00\x14\x00\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
            b"\xff\xda\x00\x0c\x03\x01\x00\x02\x11\x03\x11\x00?\x00"
            + b"\x00" * 10
            + b"\xff\xd9"
        )

    # -----------------------------------------------------------------
    # Routes
    # -----------------------------------------------------------------

    def set_restart_fn(self, fn) -> None:
        self.app.config["restart_fn"] = fn

    def setup_routes(self):
        self.app.config['backend'] = self
        self.app.config['restart_fn'] = None
        
        # --- RFC 9457 Error Handlers ---
        @self.app.errorhandler(HTTPException)
        def handle_exception(e):
            # SPA fallback: serve index.html for 404s on non-API routes
            if e.code == 404 and not request.path.startswith("/api/"):
                return send_from_directory(self.app.static_folder, "index.html")
            response = jsonify({
                "title": e.name,
                "status": e.code,
                "detail": e.description,
                "instance": request.path
            })
            response.content_type = "application/problem+json"
            return response, e.code

        @self.app.errorhandler(Exception)
        def handle_unexpected_error(e):
            logging.exception("Unexpected error: %s", e)
            response = jsonify({
                "title": "Internal Server Error",
                "status": 500,
                "detail": "An unexpected error occurred.",
                "instance": request.path
            })
            response.content_type = "application/problem+json"
            return response, 500

        from WebAPI.routes.albums import albums_bp
        from WebAPI.routes.auth import auth_bp
        from WebAPI.routes.images import images_bp
        from WebAPI.routes.maintenance import maintenance_bp
        from WebAPI.routes.profile import profile_bp
        from WebAPI.routes.settings import settings_bp
        from WebAPI.routes.sources import sources_bp
        from WebAPI.routes.stream import stream_bp

        self.app.config["backend"] = self
        self.app.register_blueprint(auth_bp)
        self.app.register_blueprint(settings_bp)
        self.app.register_blueprint(images_bp)
        self.app.register_blueprint(stream_bp)
        self.app.register_blueprint(maintenance_bp)
        self.app.register_blueprint(sources_bp)
        self.app.register_blueprint(albums_bp)
        self.app.register_blueprint(profile_bp)
        
        @self.app.route('/', defaults={'path': ''})
        @self.app.route('/<path:path>')
        def serve_react(path):
            if path and os.path.exists(os.path.join(self.app.static_folder, path)):
                return send_from_directory(self.app.static_folder, path)
            return send_from_directory(self.app.static_folder, 'index.html')

    # -----------------------------------------------------------------
    # HEIC conversion
    # -----------------------------------------------------------------

    def _normalize_existing_heic_images(self) -> None:
        """
        Scan IMAGE_DIR for any .heic/.heif files and convert them to PNG.
        """
        try:
            img_dir = Path(self.IMAGE_DIR)
            if not img_dir.is_dir():
                print(f"[Backend] IMAGE_DIR does not exist or is not a directory: {img_dir}")
                return

            heic_files = [
                p for p in img_dir.rglob("*")
                if p.is_file() and p.suffix.lower() in (".heic", ".heif")
            ]

            if not heic_files:
                print(f"[Backend] No HEIC/HEIF images to normalize under {img_dir}.")
                return

            print(
                f"[Backend] Normalizing {len(heic_files)} HEIC/HEIF image(s) "
                f"to PNG under {img_dir}..."
            )

            for entry in tqdm(heic_files, desc="HEIC->PNG", unit="img"):
                heic_path = str(entry)
                png_path = os.path.splitext(heic_path)[0] + ".png"

                out_path = self.convert_heic_to_png(heic_path, png_path)

                if out_path != heic_path and os.path.exists(out_path):
                    try:
                        self.store_image_metadata(out_path)
                    except Exception as e:
                        print(f"[Backend] Could not update metadata for {out_path}: {e}")

            print("[Backend] HEIC normalization complete.")

        except Exception as e:
            print(f"[Backend] normalize_existing_heic_images failed: {e}")


    def convert_heic_to_png(self, heic_path: str, output_path: str | None = None) -> str:
        if output_path is None:
            base, _ = os.path.splitext(heic_path)
            output_path = base + ".png"

        try:
            if "has_pillow_heif" in globals() and has_pillow_heif:
                img = Image.open(heic_path)
                try:
                    img = ImageOps.exif_transpose(img)
                except Exception:
                    pass
                img = img.convert("RGB")
                img.save(output_path, format="PNG")
                os.remove(heic_path)
                print(f"[Backend] Converted HEIC -> PNG: {heic_path} -> {output_path}")
                return output_path
        except Exception as e:
            print(f"[Backend] HEIC->PNG via Pillow failed: {e}")

        print("[Backend] HEIC conversion unavailable; keeping original file.")
        return heic_path

    # -----------------------------------------------------------------
    # Metadata broadcast
    # -----------------------------------------------------------------

    def update_current_metadata(self, metadata):
        with self._metadata_lock:
            self.latest_metadata = metadata

    # -----------------------------------------------------------------
    # Start server
    # -----------------------------------------------------------------

    def start(self):
        import socket as _socket
        host = self.host or "0.0.0.0"
        for attempt in range(10):
            try:
                s = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
                s.setsockopt(_socket.SOL_SOCKET, _socket.SO_REUSEADDR, 1)
                s.bind((host, self.port))
                s.close()
                break
            except OSError:
                print(f"[Backend] Port {self.port} busy, retrying in 3s ({attempt + 1}/10)…")
                time.sleep(3)
        try:
            from waitress import serve as _waitress_serve
            print(f"[Backend] Starting waitress on {host}:{self.port}")
            _waitress_serve(self.app, host=host, port=self.port, threads=8,
                            channel_timeout=120, cleanup_interval=30)
        except ImportError:
            print(f"[Backend] waitress not found, falling back to Flask dev server on {host}:{self.port}")
            self.app.run(
                host=host,
                port=self.port,
                debug=False,
                use_reloader=False,
                threaded=True,
            )
