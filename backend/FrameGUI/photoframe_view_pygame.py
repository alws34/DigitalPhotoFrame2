"""
Lightweight fullscreen display using pygame (SDL2).

Receives composited BGR frames (with overlays baked in) from PhotoFrameServer
and blits them directly to the screen. No encoding, no HTTP, no polling.

Render + gesture detection only (Phase 2): all settings editing lives in the
React admin UI, opened as a kiosk webview on triple-tap. This class no longer
draws its own settings panel/OSK/numpad/WiFi UI -- see
FrameGUI/kiosk/webview_launcher.py.

Requires: pygame
Display: works with Wayland, X11, DRM/KMS via SDL2 backends.
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import threading
import time as _time
from typing import Optional

import cv2
import numpy as np

try:
    import pygame
except ImportError:
    pygame = None

_TRIPLE_TAP_WINDOW = 0.8   # seconds between taps


class PhotoFramePygame:
    """
    Minimal fullscreen display. Implements the subset of iFrame that
    PhotoFrameServer actually calls: set_frame(), set_date_time(), set_weather().

    Overlays are already baked into frames by the compositor, so set_date_time
    and set_weather are no-ops here.
    """

    def __init__(self, width: int = 0, height: int = 0,
                 settings: Optional[dict] = None):
        if pygame is None:
            raise ImportError("pygame is required for display mode. "
                              "Install with: pip install pygame")

        self.settings = settings or {}
        self._running = True
        self._frame_lock = threading.Lock()
        self._pending_bgr = None
        self._last_bgr = None       # cached for brightness-only redraws

        # Triple-tap
        self._tap_times: list = []
        self._active_finger_id: "int | None" = None  # SDL2 finger tracking for single-touch

        # Software brightness (0–100); applied as dim overlay in _blit_frame
        self._screen_brightness_pct: int = 100
        self._brightness_dirty: bool = False  # signals main thread to redraw after brightness change

        # Kiosk webview: kept alive and hidden/shown via SIGUSR1 rather than
        # respawned per triple-tap, so only the very first open pays the
        # GTK/WebKit2 cold-start cost.
        self._kiosk_proc: "subprocess.Popen | None" = None
        # True while the kiosk webview fully covers this view. The main loop
        # skips render_pending_frame() while this is set -- otherwise this
        # process keeps compositing/flipping frames at ~60Hz underneath a
        # webview the user can't see, starving WebKitWebProcess of CPU and
        # making the settings UI (scrolling, popups) janky on the Pi's 4
        # cores. webview_launcher.py signals SIGUSR2 back to this process
        # (its parent) when it hides itself again.
        self._kiosk_visible: bool = False
        signal.signal(signal.SIGUSR2, self._on_kiosk_hidden)

        # pygame init
        os.environ.setdefault("SDL_VIDEO_ALLOW_SCREENSAVER", "0")
        pygame.init()

        info = pygame.display.Info()
        self.width  = width  or info.current_w
        self.height = height or info.current_h

        flags = pygame.FULLSCREEN | pygame.HWSURFACE | pygame.DOUBLEBUF
        try:
            self.screen = pygame.display.set_mode((self.width, self.height), flags)
        except pygame.error:
            self.screen = pygame.display.set_mode((self.width, self.height), pygame.FULLSCREEN)

        pygame.display.set_caption("Digital Photo Frame")
        pygame.mouse.set_visible(False)
        self.screen.fill((0, 0, 0))
        pygame.display.flip()

        logging.info("PhotoFramePygame: display %dx%d", self.width, self.height)

        from Utilities.config_events import on_settings_changed as _on_sc
        _on_sc(self._on_settings_changed_pygame)

        self._spawn_kiosk_process()  # pre-warm so the first triple-tap isn't a cold start

    def _on_settings_changed_pygame(self, new_settings: dict) -> None:
        try:
            brightness = new_settings.get("screen", {}).get("brightness")
            if brightness is not None:
                pct = int(brightness)
                was_off = (self._screen_brightness_pct == 0)
                self.set_brightness_percent(pct)
                try:
                    from Utilities.brightness import (  # noqa: I001, PLC0415
                        set_brightness_percent as _hw_brightness,
                        set_screen_power as _hw_power,
                    )
                    if pct == 0:
                        _hw_power(False)
                    else:
                        if was_off:
                            _hw_power(True)
                        _hw_brightness(pct)
                except Exception:
                    pass
        except Exception as exc:
            logging.error("PhotoFramePygame: failed to apply brightness: %s", exc)

    # ------------------------------------------------------------------
    # Frame display
    # ------------------------------------------------------------------
    def set_frame(self, bgr: np.ndarray) -> None:
        if bgr is None:
            return
        with self._frame_lock:
            self._pending_bgr = bgr

    def render_pending_frame(self) -> bool:
        with self._frame_lock:
            bgr = self._pending_bgr
            self._pending_bgr = None

        if bgr is None:
            # Re-render cached frame after a brightness-only change (thread-safe path)
            if self._brightness_dirty:
                self._brightness_dirty = False
                with self._frame_lock:
                    bgr = self._last_bgr
                if bgr is not None:
                    self._blit_frame(bgr)
                    return True
            return False

        self._brightness_dirty = False
        with self._frame_lock:
            self._last_bgr = bgr
        self._blit_frame(bgr)
        return True

    def _blit_frame(self, bgr: np.ndarray) -> None:
        try:
            rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
            h, w = rgb.shape[:2]
            surface = pygame.image.frombuffer(rgb.tobytes(), (w, h), "RGB")
            if w != self.width or h != self.height:
                surface = pygame.transform.smoothscale(surface, (self.width, self.height))
            self.screen.blit(surface, (0, 0))
            # Software brightness dim overlay (0% = black, 100% = no overlay)
            pct = self._screen_brightness_pct
            if pct < 100:
                alpha = int((100 - pct) * 255 / 100)
                dim = pygame.Surface((self.width, self.height))
                dim.fill((0, 0, 0))
                dim.set_alpha(alpha)
                self.screen.blit(dim, (0, 0))
            pygame.display.flip()
        except Exception as e:
            logging.error("Pygame render error: %s", e)

    def set_brightness_percent(self, pct: int, allow_zero: bool = True) -> bool:
        """Software brightness via a black dim overlay. 0 = screen off, 100 = full.
        Thread-safe: sets a dirty flag; the main render loop applies it within ~16 ms."""
        pct = max(0, min(100, int(pct)))
        self._screen_brightness_pct = pct
        self._brightness_dirty = True  # main thread picks this up in render_pending_frame
        return True

    def read_brightness_percent(self) -> int:
        return self._screen_brightness_pct

    # ------------------------------------------------------------------
    # Event handling (main thread)
    # ------------------------------------------------------------------
    def process_events(self) -> bool:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                return False
            if event.type == pygame.KEYDOWN and event.key in (pygame.K_ESCAPE, pygame.K_q):
                return False
            if event.type == pygame.MOUSEBUTTONDOWN:
                self._handle_triple_tap()
            # SDL2 touch events (Wayland doesn't synthesize mouse events from touch)
            if event.type == pygame.FINGERDOWN and self._active_finger_id is None:
                self._active_finger_id = event.finger_id
                self._handle_triple_tap()
            if event.type == pygame.FINGERUP and event.finger_id == self._active_finger_id:
                self._active_finger_id = None
        return True

    def _handle_triple_tap(self) -> None:
        now = _time.monotonic()
        self._tap_times = [t for t in self._tap_times if now - t < _TRIPLE_TAP_WINDOW]
        self._tap_times.append(now)
        if len(self._tap_times) >= 3:
            self._tap_times.clear()
            self._open_kiosk()

    def _open_kiosk(self) -> None:
        """Triple-tap entry point: reveal the pre-warmed kiosk webview, or
        spawn it if it's not running (first-ever tap, or the process died)."""
        if self._kiosk_proc is None or self._kiosk_proc.poll() is not None:
            self._spawn_kiosk_process()
            return
        try:
            os.kill(self._kiosk_proc.pid, signal.SIGUSR1)
            self._kiosk_visible = True
        except ProcessLookupError:
            self._spawn_kiosk_process()

    def _on_kiosk_hidden(self, _signum, _frame) -> None:
        self._kiosk_visible = False

    def is_kiosk_visible(self) -> bool:
        return self._kiosk_visible

    def _spawn_kiosk_process(self) -> None:
        """Launch the GTK+WebKit2 kiosk webview, auto-logged-in via a
        one-time in-process token. Runs as a separate top-level window;
        labwc brings it to front while this pygame process keeps rendering
        underneath. Stays alive hidden between triple-taps (see
        webview_launcher.py's SIGUSR1 handler) so only this first launch
        pays the GTK/WebKit2 cold-start cost."""
        try:
            from WebAPI.routes.kiosk import mint_kiosk_token
            token = mint_kiosk_token()
            port = self.settings.get("backend_configs", {}).get("server_port", 80)
            base = "http://127.0.0.1" if port == 80 else f"http://127.0.0.1:{port}"
            url = f"{base}/api/kiosk/login?token={token}"
            launcher = os.path.join(os.path.dirname(__file__), "kiosk", "webview_launcher.py")
            # Tried dropping LIBGL_ALWAYS_SOFTWARE for just this child so
            # WebKit could use the real VC4/V3D GPU instead of Mesa llvmpipe.
            # Corrupted the display (scanline garbage) -- labwc's own DRM/KMS
            # ownership conflicts with a second real GPU context. Reverted to
            # inheriting the full (software-forced) environment.
            self._kiosk_proc = subprocess.Popen([sys.executable, launcher, url])
        except Exception:
            logging.exception("[PhotoFramePygame] Failed to launch kiosk webview")
            self._kiosk_proc = None

    # ------------------------------------------------------------------
    # iFrame-compatible stubs
    # ------------------------------------------------------------------
    def set_date_time(self, dt_string: str = "") -> None:
        pass

    def set_weather(self, weather_data: dict = None) -> None:
        pass

    def get_is_running(self) -> bool:
        return self._running

    def get_live_frame(self):
        return None

    def update_images_list(self):
        pass

    def update_frame_to_stream(self, frame=None):
        pass

    def send_log_message(self, msg, logger=None):
        logging.info(msg)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def stop(self) -> None:
        self._running = False
        try:
            pygame.quit()
        except Exception:
            pass
