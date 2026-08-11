#!/usr/bin/env python3
"""Digital Photo Frame — display-mode runner functions."""
from __future__ import annotations

import os
import sys
import threading
import time
from typing import Any, Dict, Optional

from Utilities.autoupdate_utils import AutoUpdater
from Utilities.MQTT.mqtt_bridge import MqttBridge
from WebAPI.API import APIServer


def _restart_program():
    """Restarts the current program. Note: Executable must be executable."""
    print("[PhotoFrame] Restarting...")
    python = sys.executable
    os.execl(python, python, *sys.argv)


def _build_album_manager(settings: Dict[str, Any]):
    """
    Construct, migrate, and start AlbumManager. Returns the AlbumManager instance.
    Fails softly: logs and returns None if anything goes wrong.
    """
    try:
        from Utilities.AlbumManager import AlbumManager
        from Utilities.encryption import load_or_create_key
        from Utilities.migration import run_migrations
        from WebAPI.database import init_db

        init_db()  # ensure sources/albums tables exist before sync thread starts

        sys_cfg = settings.get("system", {})
        images_root = sys_cfg.get("image_dir") or "Images"

        encryption_key = load_or_create_key()
        run_migrations(images_root)

        album_manager = AlbumManager(images_root=images_root, encryption_key=encryption_key)
        album_manager.start()
        return album_manager
    except Exception:
        import logging
        logging.getLogger(__name__).exception(
            "[AlbumManager] Failed to start AlbumManager; playback falls back to IMAGE_DIR."
        )
        return None


def _run_headless(settings: Dict[str, Any], settings_path: str,
                  width: Optional[int], height: Optional[int]) -> None:
    from FrameServer.PhotoFrameServer import PhotoFrameServer

    backend_cfg = settings.get("backend_configs", {}) or {}
    w = width or int(backend_cfg.get("stream_width", 1920))
    h = height or int(backend_cfg.get("stream_height", 1080))

    sys_cfg = settings.get("system", {})
    images_dir = sys_cfg.get("image_dir") or settings.get(
        "image_dir") or settings.get("images_dir") or None

    print(
        f"[PhotoFrame] Headless mode. Resolution {w}x{h}. Settings: {settings_path}")

    # --- AlbumManager ---
    album_manager = _build_album_manager(settings)

    # --- AutoUpdater Setup ---
    stop_event = threading.Event()
    au_cfg = settings.get("autoupdate", {})
    updater = AutoUpdater(
        stop_event=stop_event,
        interval_sec=3600,
        restart_service_async=_restart_program,
        auto_restart_on_update=au_cfg.get("enabled", True)
    )
    updater.start()

    # Resolve active image dir from AlbumManager if available
    active_images_dir = (
        str(album_manager.get_active_image_dir()) if album_manager else images_dir
    )

    srv = PhotoFrameServer(width=w, height=h, iframe=None,
                           images_dir=active_images_dir, settings_path=settings_path)

    backend = APIServer(frame=srv, image_dir=active_images_dir)
    backend.set_restart_fn(_restart_program)
    backend.updater = updater
    if album_manager is not None:
        backend.album_manager = album_manager

        def _on_dir_change_headless(new_dir: str) -> None:
            srv.set_images_dir(new_dir)
            backend.IMAGE_DIR = backend._resolve_dir(new_dir)
            backend.THUMB_DIR = os.path.join(os.path.dirname(backend.IMAGE_DIR), "_thumbs")

        album_manager.register_dir_change_callback(_on_dir_change_headless)

    threading.Thread(target=backend.start, daemon=True).start()
    srv.m_api = backend

    t = threading.Thread(target=srv.run_photoframe, daemon=True)
    t.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        stop_event.set()
        if album_manager is not None:
            try:
                album_manager.stop()
            except Exception:
                pass
        try:
            srv.stop_services()
        except Exception:
            pass


def _run_pygame(settings: Dict[str, Any], settings_path: str) -> None:
    from FrameGUI.photoframe_view_pygame import PhotoFramePygame
    from FrameServer.PhotoFrameServer import PhotoFrameServer

    sys_cfg = settings.get("system", {})
    images_dir = sys_cfg.get("image_dir") or settings.get(
        "image_dir") or settings.get("images_dir") or None

    # --- AlbumManager ---
    album_manager = _build_album_manager(settings)

    # Create the pygame display (detects screen resolution)
    view = PhotoFramePygame(settings=settings)
    sw, sh = view.width, view.height

    print(f"[PhotoFrame] Pygame display {sw}x{sh}. Settings: {settings_path}")

    # Resolve active image dir from AlbumManager if available
    active_images_dir = (
        str(album_manager.get_active_image_dir()) if album_manager else images_dir
    )

    # Create the compositor with the display as its frame target
    srv = PhotoFrameServer(width=sw, height=sh, iframe=view,
                           images_dir=active_images_dir, settings_path=settings_path)

    # Start Flask backend
    backend = APIServer(frame=srv, image_dir=active_images_dir)
    backend.set_restart_fn(_restart_program)
    if album_manager is not None:
        backend.album_manager = album_manager

        def _on_dir_change_pygame(new_dir: str) -> None:
            srv.set_images_dir(new_dir)
            backend.IMAGE_DIR = backend._resolve_dir(new_dir)
            backend.THUMB_DIR = os.path.join(os.path.dirname(backend.IMAGE_DIR), "_thumbs")

        album_manager.register_dir_change_callback(_on_dir_change_pygame)

    threading.Thread(target=backend.start, daemon=True).start()
    srv.m_api = backend

    # AutoUpdater
    stop_event = threading.Event()
    au_cfg = settings.get("autoupdate", {})
    updater = AutoUpdater(
        stop_event=stop_event,
        interval_sec=1800,
        restart_service_async=_restart_program,
        auto_restart_on_update=au_cfg.get("enabled", True)
    )
    updater.start()
    backend.updater = updater

    # MQTT
    mqtt = MqttBridge(view=view, settings=settings)
    mqtt.start()

    # Start compositor in background thread
    t = threading.Thread(target=srv.run_photoframe, daemon=True)
    t.start()

    _port = settings.get("backend_configs", {}).get("server_port", 80)
    _url  = "http://0.0.0.0" if _port == 80 else f"http://0.0.0.0:{_port}"
    print(f"[PhotoFrame] Running. Admin UI at {_url}")

    # Main loop: process SDL events and render frames
    # pygame requires display updates from the main thread
    try:
        while view.get_is_running() and srv.get_is_running():
            if not view.process_events():
                break
            view.render_pending_frame()
            time.sleep(0.016)  # ~60 Hz event polling
    except KeyboardInterrupt:
        pass
    finally:
        stop_event.set()
        if album_manager is not None:
            try:
                album_manager.stop()
            except Exception:
                pass
        try:
            mqtt.stop()
        except Exception:
            pass
        try:
            srv.stop_services()
        except Exception:
            pass
        try:
            view.stop()
        except Exception:
            pass
