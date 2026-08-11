# region imports
import hashlib
import itertools
import logging
import math
import os
import os as _os
import random as rand
import sys
import threading
import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from enum import Enum

# Ensure FrameServer's own directory is on the path for bare sibling imports
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import cv2
import numpy as np
import psutil
from EffectHandler import EffectHandler
from image_handler import Image_Utils
from overlay import OverlayRenderer
from pillow_heif import register_heif_opener

from Utilities.brightness import get_brightness_percent
from Utilities.config_events import on_settings_changed
from Utilities.config_store import load_settings as _load_settings
from Utilities.observer import ImagesObserver
from Utilities.Weather.weather_adapter import build_weather_client

# endregion imports


class iFrame(ABC):
    @abstractmethod
    def __init__(self):
        pass

    @abstractmethod
    def send_log_message(self, msg, logger: logging):
        pass

    @abstractmethod
    def get_live_frame(self):
        pass

    @abstractmethod
    def get_is_running(self):
        pass

    @abstractmethod
    def update_images_list(self):
        pass

    @abstractmethod
    def set_frame(self):
        pass

    @abstractmethod
    def update_frame_to_stream(self):
        pass

    @abstractmethod
    def set_date_time(self):
        pass

    @abstractmethod
    def set_weather(self):
        pass


# region Logging Setup
log_file_path = os.path.join(os.path.dirname(__file__), "PhotoFrame.log")
logging.basicConfig(
    filename=log_file_path,
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
console_formatter = logging.Formatter("%(levelname)s - %(message)s")
console_handler.setFormatter(console_formatter)
logging.getLogger().addHandler(console_handler)
logging.info("PhotoFrame server starting...")
# endregion Logging Setup

SETTINGS_PATH = ""


class AnimationStatus(Enum):
    ANIMATION_FINISHED = 1
    ANIMATION_ERROR = 2


class PhotoFrameServer(iFrame):
    """
    Server owns:
      - Sizing (letterbox)
      - Date/time + weather overlay (every frame)
      - 30 fps compositor that continues between transitions
    Client displays frames only.
    """

    def __init__(self, width=1920, height=1080, iframe: iFrame = None, images_dir=None, settings_path="settings.json"):
        global SETTINGS_PATH
        SETTINGS_PATH = settings_path
        self._gui_frame = iframe

        # Instance logger (inherits global configuration)
        self.logger = logging.getLogger(__name__)

        try:
            cv2.setUseOptimized(True)
            cv2.setNumThreads(max(1, (os.cpu_count() or 4) - 1))
        except Exception as e:
            self.logger.exception("Failed to set OpenCV optimizations: %s", e)

        self._settings: dict = _load_settings()
        self._settings_lock = threading.Lock()

        if not self.set_images_dir(images_dir=images_dir):
            logging.error("Failed to set images directory. Exiting.")
            raise FileNotFoundError(
                "Images directory not found and could not be created.")

        self.screen_width = width
        self.screen_height = height

        # --- FPS from 'playback' or root ---
        playback = self._settings.get("playback", {})
        self._target_fps = int(playback.get(
            "animation_fps") or self._settings.get("animation_fps") or 30)
        self._transition_fps = int(playback.get(
            "transition_fps") or self._settings.get("transition_fps") or 30)
        self._transition_frame_interval = 1.0 / \
            max(1.0, float(self._transition_fps))

        self.current_image_idx = 0
        self.current_effect_idx = 0
        self.current_image = None
        self.next_image = None
        self.frame_to_stream = None
        self._raw_frame_to_stream = None
        self.is_running = True

        self.EffectHandler = EffectHandler()
        self.image_handler = Image_Utils(settings=self._settings)
        self.effects = self.EffectHandler.get_effects()
        self.update_images_list()

        # --- Overlay renderer (bakes date/time/weather into every frame) ---
        ui_cfg = self._settings.get("ui", {}) or {}
        font_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            ui_cfg.get("font_name", "arial.ttf"),
        )
        if not os.path.isfile(font_path):
            font_path = "arial.ttf"
        try:
            self._overlay = OverlayRenderer(
                font_path=font_path,
                time_font_size=int(ui_cfg.get("time_font_size", 80)),
                date_font_size=int(ui_cfg.get("date_font_size", 60)),
                stats_font_size=int(
                    (self._settings.get("stats", {}) or {}).get("font_size", 20)),
                desired_size=(self.screen_width, self.screen_height),
            )
        except Exception:
            logging.exception(
                "Failed to create OverlayRenderer; overlays disabled")
            self._overlay = None
        self._weather_data = {}
        self._weather_lock = threading.Lock()

        # Weather client and loop (all modes)
        self._weather_stop = threading.Event()
        self.weather_client = build_weather_client(self, self._settings)
        self._weather_thread = None
        self._start_local_weather_loop()

        # DateTime thread for GUI mode only (overlay is baked in _send_frame for all modes)
        if self._gui_frame:
            self._date_time_thread = threading.Thread(
                target=self.start_date_time_loop,
                name="DateTimeThread",
                daemon=True,
            )
            self._date_time_thread.start()
        else:
            self._date_time_thread = None

        # Filesystem observer
        self._observer_started = False
        self._observer_debounce_ts = 0.0
        self._observer_min_interval = 1.0
        self.Observer = ImagesObserver(frame=self, images_dir=self.IMAGE_DIR)
        # If your ImagesObserver supports a callback, register it:
        if hasattr(self.Observer, "on_change"):
            # type: ignore[attr-defined]
            self.Observer.on_change = self._on_images_dir_changed
        if not self._observer_started:
            self.Observer.start_observer()
            self._observer_started = True

        # Register HEIF/HEIC plugin for Pillow once per server instance
        try:
            register_heif_opener()
            logging.info("HEIF/HEIC plugin registered successfully")
        except Exception as e:
            logging.warning("Could not register HEIF/HEIC plugin: %s", e)

        self._settings_updated_flag = False
        self._settings_reload_lock = threading.Lock()

        # Compatibility shim so app.py can still pass srv.settings_handler to Backend()
        # (removed in Task 6)
        self.settings_handler = self._settings

        on_settings_changed(self._on_settings_changed)

        # Stats overlay cache
        self._stats_last_refresh: float = 0.0
        self._stats_text: str = ""
        self._stats_frame_to_stream = None

    def _blank_frame(self):
        # neutral gray, screen-sized
        return np.full((self.screen_height, self.screen_width, 3), 32, dtype=np.uint8)

    def _reload_runtime_settings(self, reload_from_disk: bool = True) -> None:
        with self._settings_reload_lock:
            if reload_from_disk:
                self._settings = _load_settings()
            playback = self._settings.get("playback", {}) or {}
            self._target_fps = int(playback.get(
                "animation_fps") or self._settings.get("animation_fps") or 30)
            self._transition_fps = int(playback.get(
                "transition_fps") or self._settings.get("transition_fps") or 30)
            self._transition_frame_interval = 1.0 / \
                max(1.0, float(self._transition_fps))

    def _on_settings_changed(self, new_data: dict) -> None:
        with self._settings_lock:
            self._settings = new_data
        # Keep the legacy alias in sync so run_photoframe() picks up new values
        self.settings_handler = new_data

        playback = new_data.get("playback", {})
        self._target_fps = int(playback.get("animation_fps", 30))
        self._transition_fps = int(playback.get(
            "transition_fps", self._target_fps))
        self._transition_frame_interval = 1.0 / \
            max(1.0, float(self._transition_fps))

        # Rebuild overlay so font size / panel changes take effect immediately
        ui_cfg = new_data.get("ui", {}) or {}
        font_path = _os.path.join(
            _os.path.dirname(_os.path.dirname(__file__)),
            ui_cfg.get("font_name", "arial.ttf"),
        )
        if not _os.path.isfile(font_path):
            font_path = "arial.ttf"
        try:
            self._overlay = OverlayRenderer(
                font_path=font_path,
                time_font_size=int(ui_cfg.get("time_font_size", 80)),
                date_font_size=int(ui_cfg.get("date_font_size", 60)),
                stats_font_size=int((new_data.get("stats", {}) or {}).get("font_size", 20)),
                desired_size=(self.screen_width, self.screen_height),
            )
        except Exception:
            pass

        # Update image handler so effects (opacity, blur, etc.) hot-reload
        try:
            self.image_handler.settings = new_data
        except Exception:
            pass

        # Rebuild weather client so location/unit changes take effect
        try:
            self.weather_client = build_weather_client(self, new_data)
        except Exception:
            pass

        self.logger.info("[PhotoFrameServer] Settings hot-reloaded")

    def apply_settings_now(self) -> bool:
        try:
            self._reload_runtime_settings(reload_from_disk=True)
            return True
        except Exception:
            logging.exception("Immediate settings apply failed")
            return False

    def _on_images_dir_changed(self):
        now = time.time()
        if now - self._observer_debounce_ts < self._observer_min_interval:
            return
        self._observer_debounce_ts = now
        try:
            self.update_images_list()
            # Keep shuffle stable if empty; otherwise reshuffle for new content
            if len(self.images) > 0:
                self.shuffled_images = self.image_handler.shuffle_images(
                    self.images)
            logging.info(
                "Images directory changed. Found %d images.", len(self.images))
        except Exception:
            logging.exception(
                "Failed to update images list after directory change")

    def start_date_time_loop(self):
        # runs in a worker thread, not the GUI thread
        while self.is_running:
            now = time.localtime()
            dt = f"{now.tm_hour:02d}:{now.tm_min:02d}:{now.tm_sec:02d}"
            # thread-safe (invokeMethod with Q_ARG or signals)
            self._gui_frame.set_date_time(dt)
            time.sleep(1)

    def _start_local_weather_loop(self) -> None:
        poll_sec = int(self._settings.get(
            "weather_poll_seconds", 900))  # default 15 min

        def _weather_loop():
            while not self._weather_stop.is_set():
                try:
                    self.weather_client.fetch()
                    data = self.weather_client.data() or {}
                    with self._weather_lock:
                        self._weather_data = data
                    # Also push to GUI if it exists
                    if self._gui_frame and hasattr(self._gui_frame, "set_weather"):
                        self._gui_frame.set_weather(data)
                except Exception:
                    logging.exception("weather loop error (server)")
                self._weather_stop.wait(poll_sec)

        self._weather_thread = threading.Thread(
            target=_weather_loop, name="WeatherThread", daemon=True)
        self._weather_thread.start()

    def _stop_weather_loop(self) -> None:
        try:
            self._weather_stop.set()
        except Exception:
            pass

    def _send_frame(self, frame_bgr: np.ndarray) -> None:
        """High-performance frame delivery for Raspberry Pi."""
        if frame_bgr is None:
            return

        # 1. Fast dimension check.
        # We assume the EffectHandler already provided a screen-sized frame.
        h, w = frame_bgr.shape[:2]
        if w != self.screen_width or h != self.screen_height:
            # Use fast cv2.resize only if the compositor failed to provide correct size
            frame_bgr = cv2.resize(frame_bgr, (self.screen_width, self.screen_height),
                                   interpolation=cv2.INTER_LINEAR)

        # 2. Bake Overlay (Date/Time/Weather)
        # Save raw frame before overlay so stream can serve clean frames.
        self._raw_frame_to_stream = frame_bgr

        # This is the most CPU-intensive part; we optimize by caching settings.
        if self._overlay:
            try:
                ui_cfg = self._settings.get("ui", {}) or {}
                show_weather = ui_cfg.get("show_weather", True)
                contrast_text = ui_cfg.get("contrast_text", False)

                # Fetch settings once per refresh
                margins = ui_cfg.get("margins", {}).copy()
                margins.setdefault("spacing_between",
                                   ui_cfg.get("spacing_between", 50))

                with self._weather_lock:
                    weather = self._weather_data if show_weather else {}

                # Optimized call (Assuming overlay.py is using Numpy blending)
                datetime_corner = ui_cfg.get("datetime_corner", "bottom-left")
                weather_corner = ui_cfg.get("weather_corner", "bottom-right")
                frame_bgr = self._overlay.render_datetime_and_weather(
                    frame_bgr, margins, weather,
                    datetime_corner=datetime_corner,
                    weather_corner=weather_corner,
                    font_color=(255, 255, 255),
                    contrast_text=contrast_text,
                )
            except Exception:
                # Fallback to raw frame if overlay fails to prevent a hard crash/freeze
                pass

        # 2b. Stats overlay (CPU/RAM/Temp/Disk/Brightness)
        if self._overlay and self._settings.get('stats', {}).get('show', False):
            try:
                now_ts = time.time()
                if now_ts - self._stats_last_refresh >= 5.0:
                    cpu = round(psutil.cpu_percent())
                    ram = round(psutil.virtual_memory().percent)
                    try:
                        temps = psutil.sensors_temperatures()
                        temp_val = None
                        if temps:
                            for key in ("coretemp", "cpu_thermal", "cpu-thermal", "k10temp", "acpitz"):
                                if key in temps and temps[key]:
                                    temp_val = round(temps[key][0].current)
                                    break
                        temp_str = f"{temp_val}°C" if temp_val is not None else "N/A"
                    except Exception:
                        temp_str = "N/A"
                    try:
                        disk_free = psutil.disk_usage('/').free / (1024 ** 3)
                        disk_str = f"{disk_free:.1f}GB"
                    except Exception:
                        disk_str = "N/A"
                    try:
                        bright = get_brightness_percent()
                        bright_str = f"{bright}%" if bright is not None else "N/A"
                    except Exception:
                        bright_str = "N/A"
                    self._stats_text = (
                        f"CPU {cpu}%  Temp {temp_str}\n"
                        f"RAM {ram}%  Disk {disk_str}\n"
                        f"Bright {bright_str}"
                    )
                    self._stats_last_refresh = now_ts
                stats_cfg = self._settings.get('stats', {})
                color = stats_cfg.get('font_color', 'white')
                corner = stats_cfg.get('corner', 'top-left')
                margin_x = int(stats_cfg.get('margin_x', 20))
                margin_y = int(stats_cfg.get('margin_y', 20))
                frame_bgr = self._overlay.render_stats(
                    frame_bgr, self._stats_text, color,
                    corner=corner, margin_x=margin_x, margin_y=margin_y,
                )
            except Exception:
                pass

        # 2c. Save stats-included frame for stream (set unconditionally so stream
        #     always has the most recent frame at this point, with or without stats).
        self._stats_frame_to_stream = frame_bgr

        # 3. Update internal buffers
        self.frame_to_stream = frame_bgr

        # 4. Dispatch to Pygame GUI (Main Thread will pick this up)
        if self._gui_frame and hasattr(self._gui_frame, "set_frame"):
            try:
                self._gui_frame.set_frame(frame_bgr)
            except Exception:
                logging.error("Failed to push frame to Pygame GUI")

        # 5. Signal the Backend MJPEG stream
        try:
            if hasattr(self, "m_api") and self.m_api:
                self.m_api._new_frame_ev.set()
        except Exception:
            pass

    # ------------- Stream API -------------
    def update_frame(self, generator):
        """
        Pull frames from the transition generator and push each one to the GUI.
        """
        last_frame = None

        try:
            # Prefer perf_counter() (monotonic, high-res) for pacing
            interval = float(
                getattr(self, "_transition_frame_interval", self._transition_frame_interval))
            now = time.perf_counter()
            next_deadline = now  # send first frame immediately

            for frame in generator:
                last_frame = frame

                # Send immediately on arrival
                self._send_frame(frame)

                # Compute next deadline and sleep just enough
                next_deadline += interval
                now = time.perf_counter()
                sleep_for = next_deadline - now
                if sleep_for > 0:
                    end = next_deadline
                    while True:
                        now = time.perf_counter()
                        remaining = end - now
                        if remaining <= 0:
                            break
                        time.sleep(min(remaining, 0.005))

            if last_frame is not None:
                self._send_frame(last_frame)

            return AnimationStatus.ANIMATION_FINISHED

        except Exception as e:
            logging.exception(f"Error during frame update: {e}")
            return AnimationStatus.ANIMATION_ERROR

    # ------------- Utils -------------
    def compute_image_hash(self, image_path):
        hash_obj = hashlib.sha256()
        with open(image_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hash_obj.update(chunk)
        return hash_obj.hexdigest()

    def get_is_running(self):
        return self.is_running

    def set_date_time(self):
        pass

    def set_weather(self):
        pass

    def get_live_frame(self):
        if isinstance(self.frame_to_stream, np.ndarray) and self.frame_to_stream.size:
            return self.frame_to_stream
        return self._blank_frame()

    def get_stream_frame(self):
        """Return the frame for the web stream — raw (no overlay) by default."""
        show_overlay = (self._settings or {}).get("stream", {}).get("show_overlay", False)
        stats_show = (self._settings or {}).get("stats", {}).get("show", False)
        if not show_overlay:
            if stats_show:
                stats_frame = getattr(self, '_stats_frame_to_stream', None)
                if isinstance(stats_frame, np.ndarray) and stats_frame.size:
                    return stats_frame
            raw = self._raw_frame_to_stream
            if isinstance(raw, np.ndarray) and raw.size:
                return raw
        return self.get_live_frame()

    def update_frame_to_stream(self, frame_bgr: np.ndarray) -> None:
        pass

    def send_log_message(self, msg, logger=None):
        try:
            lg = getattr(self, "logger", None) or logging.getLogger(__name__)
            if callable(logger):
                logger(msg)
            elif isinstance(logger, int):
                lg.log(logger, msg)
            else:
                lg.info(msg)
        except Exception:
            try:
                (lg or logging).info(msg)
            except Exception:
                print(f"[PhotoFrame LOG] {msg}")

    def get_images_from_directory(self):
        image_extensions = [
            ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".heic", ".heif",
            ".mov", ".mp4"
        ]
        image_paths = []
        for root, _dirs, files in os.walk(self.IMAGE_DIR):
            for file in files:
                if file.lower().endswith(tuple(image_extensions)):
                    image_path = os.path.join(root, file)
                    image_paths.append(image_path)
        return image_paths

    def get_random_image(self):
        if len(self.shuffled_images) == 0:
            self.shuffled_images = list(self.images)
            rand.shuffle(self.shuffled_images)
        if len(self.shuffled_images) == 0:
            return None
        self.current_image_idx = (
            self.current_image_idx + 1) % len(self.shuffled_images)
        return self.shuffled_images[self.current_image_idx]

    def set_images_dir(self, images_dir=None):
        base_root = os.path.abspath(
            os.path.join(os.path.dirname(__file__), ".."))

        old_dir = getattr(self, "IMAGE_DIR", None)

        if images_dir is not None:
            if os.path.isabs(images_dir):
                self.IMAGE_DIR = images_dir
            else:
                self.IMAGE_DIR = os.path.join(base_root, images_dir)
        else:
            # --- Search 'system' -> 'image_dir', else root ---
            sys_cfg = self._settings.get("system", {})
            cfg = sys_cfg.get("image_dir") or self._settings.get(
                "images_dir") or "Images"

            if os.path.isabs(cfg):
                self.IMAGE_DIR = cfg
            else:
                self.IMAGE_DIR = os.path.join(base_root, cfg)

        if not os.path.exists(self.IMAGE_DIR):
            os.makedirs(self.IMAGE_DIR, exist_ok=True)
            logging.warning(
                "'%s' directory not found. Created a new one.", self.IMAGE_DIR)

        logging.info("Using IMAGE_DIR = %s", self.IMAGE_DIR)

        # Restart observer on new directory so watchdog tracks the right path.
        # If the directory is unchanged, still reload; remote streaming caches
        # can mutate the active directory without a reliable watchdog event.
        if getattr(self, "_observer_started", False) and self.IMAGE_DIR != old_dir:
            try:
                self.Observer.stop_observer()
            except Exception:
                pass
            self.Observer = ImagesObserver(frame=self, images_dir=self.IMAGE_DIR)
            if hasattr(self.Observer, "on_change"):
                self.Observer.on_change = self._on_images_dir_changed
            self._observer_started = False
            self.Observer.start_observer()
            self._observer_started = True
            self.update_images_list()
        elif self.IMAGE_DIR == old_dir:
            self.update_images_list()

        return True

    def update_images_list(self):
        self.images = self.get_images_from_directory()
        self.shuffled_images = self.image_handler.shuffle_images(self.images)

    # ------------- Video / Image Loaders -------------

    def _is_video(self, path: str) -> bool:
        if not path:
            return False
        return path.lower().endswith((".mov", ".mp4"))

    def _get_first_video_frame(self, path: str):
        """Opens a video and returns the first frame as a numpy array."""
        try:
            cap = cv2.VideoCapture(path)
            if not cap.isOpened():
                return None
            ret, frame = cap.read()
            cap.release()
            if ret:
                return frame
            return None
        except Exception as e:
            logging.error(
                f"Failed to extract first frame from video {path}: {e}")
            return None

    def _video_generator(self, video_path, total_duration):
        """
        Yields frames from the video.
        - skip_background=True is used to reduce CPU load.
        - Loops based on total_duration (animation_duration + delay_between_images).
        """
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            logging.error(f"Could not open video: {video_path}")
            return

        # Video Metrics
        fps = cap.get(cv2.CAP_PROP_FPS)
        if fps <= 0:
            fps = 30
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        video_duration = total_frames / fps

        # --- Rule: Delete if > 30 seconds (unchanged) ---
        if video_duration > 30.0:
            cap.release()
            logging.warning(
                f"Video {os.path.basename(video_path)} is too long ({video_duration:.2f}s). Deleting.")
            try:
                os.remove(video_path)
                self.update_images_list()
            except Exception as e:
                logging.error(f"Failed to delete long video: {e}")
            return

        # --- Calculate Loops for TOTAL duration ---
        # We ensure the video plays for at least the total time required
        if video_duration >= total_duration:
            loop_count = 1
        else:
            loop_count = math.ceil(total_duration / video_duration)
            loop_count = max(loop_count, 1)

        logging.info(
            f"Playing Video: {os.path.basename(video_path)} | VidLen: {video_duration:.1f}s | Target: {total_duration}s | Loops: {loop_count}")

        frame_interval = 1.0 / fps

        for i in range(int(loop_count)):
            cap.set(cv2.CAP_PROP_POS_FRAMES, 0)

            loop_start_time = time.perf_counter()

            while True:
                now = time.perf_counter()
                elapsed_since_start = now - loop_start_time

                target_frame_index = int(elapsed_since_start * fps)
                current_frame_index = int(cap.get(cv2.CAP_PROP_POS_FRAMES))

                if target_frame_index > current_frame_index:
                    frames_to_skip = target_frame_index - current_frame_index
                    if frames_to_skip > 0:
                        for _ in range(frames_to_skip):
                            cap.grab()

                ret, frame = cap.read()
                if not ret:
                    break

                # OPTIMIZATION: Pass skip_background=True here
                resized_frame = self.image_handler.resize_image_with_background(
                    frame, self.screen_width, self.screen_height, skip_background=True
                )
                yield resized_frame

                after_process = time.perf_counter()
                next_frame_time = loop_start_time + \
                    ((target_frame_index + 1) * frame_interval)
                sleep_time = next_frame_time - after_process

                if sleep_time > 0:
                    time.sleep(sleep_time)

            # Optional: 1.5s Pause between loops (matches your previous style)
            if i < (loop_count - 1):
                if 'resized_frame' in locals():
                    pause_start = time.perf_counter()
                    while (time.perf_counter() - pause_start) < 1.5:
                        yield resized_frame
                        time.sleep(0.05)

        cap.release()

    def _load_image_safe(self, path: str):
        if self._is_video(path):
            return self._get_first_video_frame(path)

        if not path:
            logging.warning(
                "PhotoFrameServer._load_image_safe: empty image path")
            return None
        if not os.path.isfile(path):
            logging.warning(
                "PhotoFrameServer._load_image_safe: missing image file %r", path)
            return None

        ext = os.path.splitext(path)[1].lower()

        # HEIC/HEIF path
        if ext in (".heic", ".heif"):
            try:
                try:
                    from PIL import Image, ImageOps
                    pil_img = Image.open(path)
                    try:
                        pil_img = ImageOps.exif_transpose(pil_img)
                    except Exception:
                        pass
                    pil_img = pil_img.convert("RGB")
                    arr = np.array(pil_img)
                    arr = arr[:, :, ::-1].copy()
                    return arr
                except Exception as e:
                    logging.warning(
                        "PhotoFrameServer: Pillow HEIC decode failed for %r: %s", path, e)

                try:
                    import pyheif

                    heif = pyheif.read(path)
                    pil_img = Image.frombytes(
                        heif.mode, heif.size, heif.data, "raw", heif.mode, heif.stride)
                    pil_img = pil_img.convert("RGB")
                    arr = np.array(pil_img)
                    arr = arr[:, :, ::-1].copy()
                    return arr
                except Exception as e:
                    logging.warning(
                        "PhotoFrameServer: HEIC decode failed for %r via pyheif: %s", path, e)
                    return None
            except Exception:
                logging.exception(
                    "PhotoFrameServer: HEIC decode crashed for %r", path)
                return None

        img = cv2.imread(path)
        if img is None:
            logging.warning("PhotoFrameServer: cv2.imread failed for %r", path)
            return None

        return img

    # ------------- Main Transition Logic -------------

    def start_image_transition(self, image1_path=None, image2_path=None, duration=5, hold_time=0):
        if self.current_image is None:
            first_path = image1_path or self.get_random_image()
            img1 = self._load_image_safe(first_path)
            if img1 is None:
                self.current_image = self._blank_frame()
                self._send_frame(self.current_image)
                return False  # Not a video

            self.current_image = self.image_handler.resize_image_with_background(
                img1, self.screen_width, self.screen_height
            )

        if image2_path is None:
            image2_path = self.get_random_image()

        is_video_transition = self._is_video(image2_path)

        if is_video_transition:
            img2 = self._get_first_video_frame(image2_path)
        else:
            img2 = self._load_image_safe(image2_path)

        if img2 is None:
            logging.error(
                "start_image_transition: failed to load next media. Skipping.")
            self._send_frame(self.current_image)
            return False

        try:
            self.update_image_metadata(image2_path)
        except Exception:
            pass

        self.next_image = self.image_handler.resize_image_with_background(
            img2, self.screen_width, self.screen_height
        )

        effect_function = self.effects[self.EffectHandler.get_random_effect()]
        transition_gen = effect_function(
            self.current_image, self.next_image, duration, fps=self._target_fps)

        final_generator = transition_gen

        if is_video_transition:
            # Pass (duration + hold_time) so the video loops for the full experience
            # This covers the transition AND the delay
            video_gen = self._video_generator(
                image2_path, duration + hold_time)
            final_generator = itertools.chain(transition_gen, video_gen)

        self.status = self.update_frame(final_generator)

        if self.status == AnimationStatus.ANIMATION_FINISHED:
            self.current_image = self.next_image

        return is_video_transition

    def set_frame(self, frame):
        pass

    # ------------- Main loops -------------
    def run_photoframe(self):
        self.shuffled_images = self.image_handler.shuffle_images(self.images)
        img_path = self.get_random_image()

        # Initial image load
        if img_path:
            img = self._load_image_safe(img_path)
            if img is not None:
                self.current_image = self.image_handler.resize_image_with_background(
                    img, self.screen_width, self.screen_height
                )
            else:
                self.current_image = self._blank_frame()
        else:
            self.current_image = self._blank_frame()

        self._send_frame(self.current_image)

        while self.is_running:
            playback = self.settings_handler.get("playback", {})
            anim_duration = playback.get("animation_duration") or 10
            delay = playback.get("delay_between_images") or 30

            if anim_duration > 0:
                is_video = self.start_image_transition(
                    duration=anim_duration, hold_time=delay)

                if not is_video:
                    hold_until = time.time() + delay
                    next_tick = time.time()
                    while time.time() < hold_until and self.is_running:
                        self._send_frame(self.current_image)

                        # PRECISE TIMING: Calculate next exact second
                        next_tick += 1.0
                        sleep_time = next_tick - time.time()
                        if sleep_time > 0:
                            time.sleep(sleep_time)
            else:
                self._send_frame(self.current_image)
                time.sleep(1.0)

    def stop_services(self) -> None:
        def _join(th, name: str, timeout: float = 2.0) -> None:
            if th is None:
                return
            try:
                if th.is_alive():
                    th.join(timeout=timeout)
            except Exception:
                logging.exception("Failed joining thread %s", name)

        logging.info("PhotoFrameServer.stop_services: stopping...")
        self.is_running = False

        try:
            if hasattr(self, "_weather_stop") and self._weather_stop:
                self._weather_stop.set()
        except Exception:
            pass

        try:
            if hasattr(self, "Observer") and self.Observer:
                stop_fn = getattr(self.Observer, "stop_observer", None)
                if callable(stop_fn):
                    stop_fn()
                else:
                    close_fn = getattr(self.Observer, "close", None)
                    if callable(close_fn):
                        close_fn()
        except Exception:
            logging.exception("Failed to stop ImagesObserver")

        try:
            if hasattr(self, "m_api") and self.m_api:
                for meth in ("stop", "shutdown", "close"):
                    fn = getattr(self.m_api, meth, None)
                    if callable(fn):
                        try:
                            fn()
                        except Exception:
                            logging.exception("Backend.%s() failed", meth)
        except Exception:
            logging.exception("Failed to stop Backend API")

        _join(getattr(self, "_date_time_thread", None), "DateTimeThread")
        _join(getattr(self, "_weather_thread", None), "WeatherThread")

        try:
            if self._gui_frame and hasattr(self._gui_frame, "stop"):
                self._gui_frame.stop()
        except Exception:
            logging.exception("GUI stop failed")

        logging.info("PhotoFrameServer.stop_services: done.")

    # ------------- Metadata (server-owned, stored in SQLite images_metadata) -------------

    def _utcnow_iso(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def _file_mtime_iso(self, path: str) -> str:
        try:
            ts = os.path.getmtime(path)
            return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        except Exception:
            return None

    def _extract_exif_datetime(self, path: str) -> str:
        try:
            from PIL import ExifTags, Image
            img = Image.open(path)
            exif = img.getexif()
            if not exif:
                return None
            key_map = {ExifTags.TAGS.get(k, str(k)): k for k in exif.keys()}
            for tag_name in ("DateTimeOriginal", "DateTimeDigitized", "DateTime"):
                k = key_map.get(tag_name)
                if k is None:
                    continue
                raw = exif.get(k)
                if not raw:
                    continue
                try:
                    dt = datetime.strptime(str(raw), "%Y:%m:%d %H:%M:%S")
                    return dt.replace(tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                except Exception:
                    continue
            return None
        except Exception:
            return None

    def update_image_metadata(self, image_path: str) -> None:
        try:
            if not image_path or not os.path.isfile(image_path):
                return

            from WebAPI.database import get_metadata, update_metadata

            file_hash = self.compute_image_hash(image_path)
            entry = get_metadata(file_hash) or {}

            try:
                st = os.stat(image_path)
                fsize = int(st.st_size)
            except Exception:
                fsize = None

            width = entry.get("width")
            height = entry.get("height")
            if width is None or height is None:
                try:
                    if self._is_video(image_path):
                        cap = cv2.VideoCapture(image_path)
                        if cap.isOpened():
                            width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                            height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                            cap.release()
                    else:
                        img = cv2.imread(image_path)
                        if img is not None:
                            height, width = int(
                                img.shape[0]), int(img.shape[1])
                except Exception:
                    pass

            date_taken = entry.get("date_taken")
            if not date_taken and not self._is_video(image_path):
                date_taken = self._extract_exif_datetime(image_path)

            now_iso = self._utcnow_iso()
            date_added = entry.get("date_added") or now_iso
            date_modified = self._file_mtime_iso(image_path)

            filename = os.path.basename(image_path)
            try:
                rel_path = os.path.relpath(image_path, self.IMAGE_DIR)
            except Exception:
                rel_path = image_path

            views = int(entry.get("views") or 0) + 1
            caption = entry.get("caption") or "N/A"
            uploader = entry.get("uploader")

            updated = {
                "hash": file_hash,
                "filename": filename,
                "relative_path": rel_path,
                "absolute_path": image_path,
                "filesize": fsize,
                "width": width,
                "height": height,
                "date_taken": date_taken,
                "date_added": date_added,
                "date_modified": date_modified,
                "last_displayed": now_iso,
                "views": views,
                "caption": caption,
                "uploader": uploader,
            }

            update_metadata(file_hash, updated)

            try:
                self.current_metadata = updated
            except Exception:
                pass

        except Exception:
            logging.exception("update_image_metadata failed")


if __name__ == "__main__":
    frame = PhotoFrameServer()
    frame.main()
