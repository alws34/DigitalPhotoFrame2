"""
Lightweight fullscreen display using pygame (SDL2).

Receives composited BGR frames (with overlays baked in) from PhotoFrameServer
and blits them directly to the screen. No encoding, no HTTP, no polling.

Requires: pygame
Display: works with Wayland, X11, DRM/KMS via SDL2 backends.
"""

from __future__ import annotations

import logging
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time as _time
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np

try:
    import pygame
except ImportError:
    pygame = None

_TRIPLE_TAP_WINDOW = 0.8   # seconds between taps

# Tab bar: index 0 is always QR, 1+ are settings tabs
_TABS = ["QR", "WiFi", "Playback", "Display", "Weather", "Network", "System"]

# Which top-level settings sections go in each tab (tab index → list of section keys)
# Tab 0 (QR) and Tab 1 (WiFi) are custom-drawn; settings tabs start at 2.
_TAB_SECTIONS: Dict[int, List[str]] = {
    2: ["playback"],
    3: ["ui", "screen", "stats", "effects"],
    4: ["open_meteo"],
    5: ["backend_configs", "mqtt"],
    6: ["system", "autoupdate"],
}



# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_nested(d: dict, path: str, default=None):
    parts = path.split(".")
    cur = d
    for p in parts:
        if not isinstance(cur, dict) or p not in cur:
            return default
        cur = cur[p]
    return cur


def _set_nested(d: dict, path: str, value) -> None:
    parts = path.split(".")
    cur = d
    for p in parts[:-1]:
        cur = cur.setdefault(p, {})
    cur[parts[-1]] = value


def _deep_update(base: dict, updates: dict) -> None:
    for key, val in updates.items():
        if isinstance(val, dict) and isinstance(base.get(key), dict):
            _deep_update(base[key], val)
        else:
            base[key] = val


def _collect_dotted_keys(d: dict, prefix: str = "") -> list:
    keys = []
    for k, v in d.items():
        path = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            keys.extend(_collect_dotted_keys(v, path))
        else:
            keys.append(path)
    return keys


def _build_fields(settings: dict, sections: List[str]) -> List[tuple]:
    """
    Return flat list of field descriptors for the given section keys.
    Each entry is one of:
      ("header", label_text)
      (path, label, ftype, meta)
        where meta is:
          for bool:                   None
          for int/float:              (step, fmin, fmax)
          for enum/color:             choices_list
          for str/password/numeric_string: None
    """
    from Utilities.config_store import get_field_schema
    result: List[tuple] = []
    show_headers = len(sections) > 1

    def _recurse(data: dict, prefix: str, depth: int) -> None:
        for key, val in data.items():
            path = f"{prefix}.{key}"
            schema = get_field_schema(path)

            label = "  " * depth + key.replace("_", " ").title()
            if schema and schema.get("restart_required"):
                label += " ⚠"

            if schema is None:
                # No schema = container node or unregistered field
                if isinstance(val, dict):
                    result.append(("header", label))
                    _recurse(val, path, depth + 1)
                # skip lists and unregistered leaf values
                continue

            ftype = schema["type"]

            if ftype == "bool":
                result.append((path, label, "bool", None))

            elif ftype in ("int", "float"):
                py_type = int if ftype == "int" else float
                step = schema.get("step", 1 if ftype == "int" else 0.05)
                fmin = schema.get("min", -999999)
                fmax = schema.get("max",  999999)
                result.append((path, label, py_type, (step, fmin, fmax)))

            elif ftype in ("enum", "color"):
                if schema.get("ui") == "timezone_select":
                    result.append((path, label, "str", None))
                else:
                    result.append((path, label, "cycle", schema.get("choices", [])))

            elif ftype in ("str", "password", "numeric_string"):
                result.append((path, label, ftype, None))

    for section_key in sections:
        section_data = settings.get(section_key)
        if not isinstance(section_data, dict):
            continue
        if show_headers:
            result.append(("header", section_key.replace("_", " ").upper()))
        _recurse(section_data, section_key, 0)

    return result


# ---------------------------------------------------------------------------
# Main class
# ---------------------------------------------------------------------------

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
        self._last_bgr = None       # cached for panel redraws between frames

        # Triple-tap
        self._tap_times: list = []

        # Panel / settings form state
        self._panel_visible = False
        self._active_tab = 0
        self._live_settings: dict = {}
        self._pending_changes: dict = {}
        self._save_msg: str = ""
        self._save_msg_until: float = 0.0
        self._scroll_offsets: Dict[int, int] = {i: 0 for i in range(len(_TABS))}
        self._tab_scroll_x: int = 0   # horizontal scroll offset for the tab bar
        # Cached field lists per tab (rebuilt when panel opens)
        self._tab_fields: Dict[int, List[tuple]] = {}

        # QR
        self._info_url: str = ""
        self._qr_surface: Optional[pygame.Surface] = None

        # Interactive element hit rects: (pygame.Rect, action, data)
        self._ui_rects: List[Tuple[pygame.Rect, str, Any]] = []

        # Restart prompt overlay
        self._restart_prompt: bool = False
        self._stop_prompt: bool = False

        # Touch drag-to-scroll tracking
        self._drag_origin: "tuple[int, int] | None" = None  # original press position
        self._drag_start: "tuple[int, int] | None" = None   # last position (for delta)
        self._drag_scrolled: bool = False  # True if total displacement > tap threshold
        self._active_finger_id: "int | None" = None  # SDL2 finger tracking for single-touch

        # Numpad overlay
        self._numpad_active: bool = False
        self._numpad_field_path: "str | None" = None
        self._numpad_buffer: str = ""
        self._numpad_field_meta: "tuple | None" = None  # (py_type, fmin, fmax) for int/float

        # OSK state
        self._osk_active: bool = False
        self._osk_field_path: "str | None" = None
        self._osk_buffer: str = ""
        self._osk_masked: bool = False
        self._osk_proc: "subprocess.Popen | None" = None
        self._osk_use_subprocess: bool = False
        self._osk_shift: bool = False
        self._osk_sym_mode: bool = False
        self._osk_label: str = ""
        self._osk_target: str = "field"  # "field" or "wifi"

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

        # WiFi state (Network tab)
        import shutil as _shutil
        self._wifi_available: bool = bool(_shutil.which("nmcli"))
        self._wifi_networks: list = []
        self._wifi_scanning: bool = False
        self._wifi_selected_ssid: "str | None" = None
        self._wifi_msg: str = ""
        self._wifi_msg_until: float = 0.0

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

        pygame.font.init()
        H = self.height
        self._font_title   = pygame.font.SysFont("sans",      max(32, H // 18), bold=True)
        self._font_tab     = pygame.font.SysFont("sans",      max(18, H // 36))
        self._font_label   = pygame.font.SysFont("sans",      max(16, H // 40))
        self._font_value   = pygame.font.SysFont("monospace", max(18, H // 36), bold=True)
        self._font_url     = pygame.font.SysFont("monospace", max(22, H // 28))
        self._font_section = pygame.font.SysFont("sans",      max(14, H // 48), bold=True)

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
            # Re-render from cache for panel redraws or brightness changes (thread-safe path)
            if self._panel_visible or self._brightness_dirty:
                self._brightness_dirty = False
                with self._frame_lock:
                    bgr = self._last_bgr
                if bgr is None and self._panel_visible:
                    # No photo cached yet; render panel on a black background so
                    # _ui_rects is populated before the first frame arrives.
                    bgr = np.zeros((self.height, self.width, 3), dtype=np.uint8)
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
            if self._panel_visible:
                self._draw_panel()
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
    # Panel drawing
    # ------------------------------------------------------------------
    def _draw_panel(self) -> None:
        W, H = self.width, self.height
        pad = max(14, H // 45)

        panel = pygame.Surface((W, H), pygame.SRCALPHA)
        panel.fill((8, 12, 28, 255))  # fully opaque

        self._ui_rects = []

        # ── Tab bar ────────────────────────────────────────────────
        TAB_H  = max(48, H // 14)
        n_tabs = len(_TABS)
        # X button is fixed at the far right
        x_btn_w = TAB_H
        avail_w = W - x_btn_w - 4       # pixels available for the tab strip

        # Minimum tab width; decide whether scrolling is needed
        TAB_MIN_W = max(70, H // 11)
        total_tab_w = n_tabs * TAB_MIN_W
        needs_scroll = total_tab_w > avail_w
        tab_w = TAB_MIN_W if needs_scroll else (avail_w // n_tabs)

        ARR_W = TAB_H  # arrow buttons are the same height as the tab bar (square)

        if needs_scroll:
            max_tab_scroll = max(0, total_tab_w - avail_w + ARR_W * 2)
            self._tab_scroll_x = max(0, min(self._tab_scroll_x, max_tab_scroll))
            can_left  = self._tab_scroll_x > 0
            can_right = self._tab_scroll_x < max_tab_scroll
        else:
            can_left = can_right = False
            self._tab_scroll_x = 0

        # Viewport for the tab strip (between optional arrow buttons)
        vp_x = ARR_W if can_left else 0
        vp_right = avail_w - (ARR_W if can_right else 0)
        vp_w = vp_right - vp_x

        # Clip so scrolling tabs don't bleed into arrow / X areas
        panel.set_clip(pygame.Rect(vp_x, 0, vp_w, TAB_H))
        for i, name in enumerate(_TABS):
            tx = vp_x + i * tab_w - self._tab_scroll_x
            tr = pygame.Rect(tx + 2, 3, tab_w - 4, TAB_H - 6)
            active = (i == self._active_tab)
            pygame.draw.rect(panel,
                             (60, 110, 255, 255) if active else (30, 40, 80, 220),
                             tr, border_radius=8)
            t = self._font_tab.render(name, True, (255, 255, 255))
            panel.blit(t, (tr.centerx - t.get_width() // 2,
                           tr.centery - t.get_height() // 2))
            # Only register tap if the tab center is within the viewport
            if vp_x <= tr.centerx < vp_right:
                self._ui_rects.append((tr, "tab", i))
        panel.set_clip(None)

        # Left scroll arrow
        if can_left:
            la = pygame.Rect(2, 3, ARR_W - 4, TAB_H - 6)
            pygame.draw.rect(panel, (50, 80, 160, 230), la, border_radius=8)
            lt = self._font_tab.render("◀", True, (255, 255, 255))
            panel.blit(lt, (la.centerx - lt.get_width() // 2,
                            la.centery - lt.get_height() // 2))
            self._ui_rects.insert(0, (la, "tab_scroll_left", None))

        # Right scroll arrow
        if can_right:
            ra = pygame.Rect(avail_w - ARR_W + 2, 3, ARR_W - 4, TAB_H - 6)
            pygame.draw.rect(panel, (50, 80, 160, 230), ra, border_radius=8)
            rt = self._font_tab.render("▶", True, (255, 255, 255))
            panel.blit(rt, (ra.centerx - rt.get_width() // 2,
                            ra.centery - rt.get_height() // 2))
            self._ui_rects.insert(0, (ra, "tab_scroll_right", None))

        # X button (always fixed at the right)
        xr = pygame.Rect(W - x_btn_w + 3, 3, x_btn_w - 6, TAB_H - 6)
        pygame.draw.rect(panel, (160, 40, 40, 255), xr, border_radius=8)
        xt = self._font_tab.render("X", True, (255, 255, 255))
        panel.blit(xt, (xr.centerx - xt.get_width() // 2,
                        xr.centery - xt.get_height() // 2))
        self._ui_rects.append((xr, "close", None))

        pygame.draw.line(panel, (60, 110, 255, 100), (0, TAB_H), (W, TAB_H), 1)

        content_top    = TAB_H + pad // 2
        content_bottom = H - pad

        if self._active_tab == 0:
            self._draw_qr_tab(panel, content_top, content_bottom, pad)
        elif self._active_tab == 1:  # WiFi tab
            self._draw_wifi_tab(panel, content_top, content_bottom, pad)
        else:
            self._draw_settings_tab(panel, content_top, content_bottom, pad)

        if self._numpad_active:
            self._draw_numpad(panel)

        if self._osk_active:
            self._draw_osk_overlay(panel)

        if self._restart_prompt:
            self._draw_restart_prompt(panel)

        if self._stop_prompt:
            self._draw_stop_prompt(panel)

        self.screen.blit(panel, (0, 0))

    # ------------------------------------------------------------------
    # QR tab
    # ------------------------------------------------------------------
    def _draw_qr_tab(self, panel, top: int, bottom: int, pad: int) -> None:
        W = self.width
        port = self.settings.get("backend_configs", {}).get("server_port", 80)
        url  = f"http://{self._info_url}" if port == 80 else f"http://{self._info_url}:{port}"
        y    = top

        title = self._font_title.render("  Settings & Admin", True, (255, 255, 255))
        panel.blit(title, ((W - title.get_width()) // 2, y))
        y += title.get_height() + pad

        if self._qr_surface is not None:
            avail_h  = bottom - y - 80   # leave room for URL + labels
            qr_size  = max(120, min(W - pad * 4, avail_h) * 7 // 10)
            qr_scaled = pygame.transform.smoothscale(self._qr_surface, (qr_size, qr_size))
            border    = 10
            bg_w, bg_h = qr_size + border * 2, qr_size + border * 2
            qr_bg = pygame.Surface((bg_w, bg_h))
            qr_bg.fill((255, 255, 255))
            qr_bg.blit(qr_scaled, (border, border))
            panel.blit(qr_bg, ((W - bg_w) // 2, y))
            y += bg_h + pad

        url_surf = self._font_url.render(url, True, (140, 200, 255))
        uw, uh   = url_surf.get_size()
        pp       = 12
        pill     = pygame.Surface((uw + pp * 2, uh + pp * 2), pygame.SRCALPHA)
        pill.fill((20, 30, 70, 180))
        pygame.draw.rect(pill, (60, 110, 255, 120), pill.get_rect(), 1)
        pill.blit(url_surf, (pp, pp))
        panel.blit(pill, ((W - pill.get_width()) // 2, y))
        y += pill.get_height() + pad // 2

        for txt, col in [
            ("Scan QR or open URL in any browser on your network", (180, 180, 200)),
            ("Settings  |  Gallery  |  Upload photos",             (130, 160, 220)),
        ]:
            s = self._font_label.render(txt, True, col)
            panel.blit(s, ((W - s.get_width()) // 2, y))
            y += s.get_height() + 6

    # ------------------------------------------------------------------
    # Settings form tab
    # ------------------------------------------------------------------
    def _draw_settings_tab(self, panel, top: int, bottom: int, pad: int) -> None:
        W       = self.width
        tab_idx = self._active_tab
        fields  = self._tab_fields.get(tab_idx, [])

        ROW_H   = max(46, self.height // 16)
        HDR_H   = max(26, self.height // 28)
        BTN_W   = max(44, self.height // 16)
        VAL_W   = max(72, self.height // 10)
        SAVE_H  = max(44, self.height // 16)

        content_top    = top
        content_bottom = bottom - SAVE_H - pad
        avail_h        = content_bottom - content_top

        # Compute total scrollable height
        total_h = sum(HDR_H + 4 if f[0] == "header" else ROW_H + 4
                      for f in fields)
        max_scroll = max(0, total_h - avail_h)
        scroll     = max(0, min(self._scroll_offsets.get(tab_idx, 0), max_scroll))
        self._scroll_offsets[tab_idx] = scroll

        # Merged current + pending
        merged: dict = {}
        _deep_update(merged, self._live_settings)
        _deep_update(merged, self._pending_changes)

        panel.set_clip(pygame.Rect(0, content_top, W, avail_h))

        y_cursor = content_top - scroll

        for field in fields:
            if field[0] == "header":
                _, hdr_label = field
                row_h = HDR_H + 4
                ry    = y_cursor
                y_cursor += row_h
                if ry + row_h < content_top or ry > content_bottom:
                    continue
                hs = self._font_section.render(hdr_label, True, (100, 150, 255))
                panel.blit(hs, (pad + 4, ry + (HDR_H - hs.get_height()) // 2))
                pygame.draw.line(panel, (60, 90, 180, 120),
                                 (pad, ry + HDR_H), (W - pad, ry + HDR_H), 1)
                continue

            path, label, ftype, meta = field
            row_h  = ROW_H + 4
            row_y  = y_cursor
            y_cursor += row_h
            if row_y + row_h <= content_top or row_y >= content_bottom:
                continue

            row_rect = pygame.Rect(pad, row_y, W - pad * 2, ROW_H)
            rs = pygame.Surface((row_rect.w, row_rect.h), pygame.SRCALPHA)
            rs.fill((20, 28, 55, 150))
            panel.blit(rs, row_rect.topleft)
            pygame.draw.rect(panel, (60, 90, 180, 70), row_rect, 1)

            lbl = self._font_label.render(label, True, (200, 210, 240))
            panel.blit(lbl, (pad + 8, row_y + (ROW_H - lbl.get_height()) // 2))

            val     = _get_nested(merged, path)
            btn_h   = ROW_H - pad
            btn_y   = row_y + (ROW_H - btn_h) // 2
            right_x = W - pad

            if ftype == "bool":
                cur = bool(_get_nested(merged, path, False))
                tgl_w = max(72, self.height // 14)
                tgl   = pygame.Rect(right_x - tgl_w, btn_y, tgl_w, btn_h)
                pygame.draw.rect(panel,
                                 (30, 160, 80, 220) if cur else (100, 40, 40, 220),
                                 tgl, border_radius=8)
                ts = self._font_value.render("ON" if cur else "OFF", True, (255, 255, 255))
                panel.blit(ts, (tgl.centerx - ts.get_width() // 2,
                                tgl.centery  - ts.get_height() // 2))
                self._ui_rects.append((tgl, "toggle", path))

            elif ftype == "cycle":
                choices = meta
                cur_str = str(val) if val is not None else (choices[0] if choices else "")
                next_r  = pygame.Rect(right_x - BTN_W, btn_y, BTN_W, btn_h)
                vw      = VAL_W + 16
                val_r   = pygame.Rect(next_r.left - vw - 4, btn_y, vw, btn_h)
                prev_r  = pygame.Rect(val_r.left - BTN_W - 4, btn_y, BTN_W, btn_h)
                for r, txt, act, dat in [
                    (prev_r, "<", "cycle_prev", (path, choices)),
                    (next_r, ">", "cycle_next", (path, choices)),
                ]:
                    pygame.draw.rect(panel, (40, 60, 140, 220), r, border_radius=6)
                    s = self._font_value.render(txt, True, (255, 255, 255))
                    panel.blit(s, (r.centerx - s.get_width() // 2,
                                   r.centery  - s.get_height() // 2))
                    self._ui_rects.append((r, act, dat))
                pygame.draw.rect(panel, (15, 22, 50, 200), val_r)
                vs = self._font_value.render(cur_str, True, (200, 230, 255))
                panel.blit(vs, (val_r.centerx - vs.get_width() // 2,
                                val_r.centery  - vs.get_height() // 2))

            elif ftype in ("str", "password", "numeric_string"):
                cur_str = str(_get_nested(merged, path, ""))
                display = ("*" * min(len(cur_str), 16)) if ftype == "password" else cur_str
                val_surf = self._font_value.render(display[:24] or "(tap to edit)", True, (200, 230, 255))
                tap_w = max(200, W // 4)
                tap_r = pygame.Rect(right_x - tap_w, btn_y, tap_w, btn_h)
                pygame.draw.rect(panel, (40, 60, 140, 220), tap_r, border_radius=6)
                panel.blit(val_surf, (tap_r.centerx - val_surf.get_width() // 2,
                                      tap_r.centery - val_surf.get_height() // 2))
                self._ui_rects.append((tap_r, f"edit_{ftype}", path))

            else:  # int or float — +/- buttons
                step, fmin, fmax = meta
                py_type = ftype  # already int or float class
                try:
                    cur_num = py_type(_get_nested(merged, path, 0))
                except Exception:
                    cur_num = py_type(0)
                plus_r  = pygame.Rect(right_x - BTN_W, btn_y, BTN_W, btn_h)
                val_r   = pygame.Rect(plus_r.left - VAL_W - 4, btn_y, VAL_W, btn_h)
                minus_r = pygame.Rect(val_r.left  - BTN_W - 4, btn_y, BTN_W, btn_h)
                for r, txt in [(minus_r, "-"), (plus_r, "+")]:
                    pygame.draw.rect(panel, (40, 60, 140, 220), r, border_radius=6)
                    s = self._font_value.render(txt, True, (255, 255, 255))
                    panel.blit(s, (r.centerx - s.get_width() // 2,
                                   r.centery  - s.get_height() // 2))
                pygame.draw.rect(panel, (15, 22, 50, 200), val_r)
                pygame.draw.rect(panel, (60, 100, 200, 180), val_r, 1)  # tappable hint
                v_text = f"{cur_num:.2f}" if py_type is float else str(int(cur_num))
                vs = self._font_value.render(v_text, True, (200, 230, 255))
                panel.blit(vs, (val_r.centerx - vs.get_width() // 2,
                                val_r.centery  - vs.get_height() // 2))
                self._ui_rects.append((minus_r, "dec", (path, py_type, step, fmin, fmax)))
                self._ui_rects.append((plus_r,  "inc", (path, py_type, step, fmin, fmax)))
                self._ui_rects.append((val_r, "edit_number", (path, py_type, fmin, fmax)))

        panel.set_clip(None)

        # Scroll arrows — full-width tappable bars
        ARR_H = max(36, self.height // 18)
        ARR_COLOR = (50, 80, 180, 200)
        if scroll > 0:
            up_r = pygame.Rect(pad, content_top, W - pad * 2, ARR_H)
            pygame.draw.rect(panel, ARR_COLOR, up_r, border_radius=6)
            up_s = self._font_label.render("▲  scroll up", True, (200, 220, 255))
            panel.blit(up_s, (up_r.centerx - up_s.get_width() // 2,
                               up_r.centery - up_s.get_height() // 2))
            self._ui_rects.append((up_r, "scroll_up", tab_idx))
        if scroll < max_scroll:
            dn_r = pygame.Rect(pad, content_bottom - ARR_H, W - pad * 2, ARR_H)
            pygame.draw.rect(panel, ARR_COLOR, dn_r, border_radius=6)
            dn_s = self._font_label.render("▼  scroll down", True, (200, 220, 255))
            panel.blit(dn_s, (dn_r.centerx - dn_s.get_width() // 2,
                               dn_r.centery - dn_s.get_height() // 2))
            self._ui_rects.append((dn_r, "scroll_down", tab_idx))

        # Bottom action bar — System tab gets Save + Restart + Stop; others get Save only
        btn_y = bottom - SAVE_H
        is_system_tab = (tab_idx == len(_TABS) - 1)  # System is last tab
        if is_system_tab:
            action_w = max(140, W // 6)
            gap = max(10, W // 40)
            total_w = action_w * 3 + gap * 2
            bx = (W - total_w) // 2
            save_rect    = pygame.Rect(bx,                        btn_y, action_w, SAVE_H)
            restart_rect = pygame.Rect(bx + action_w + gap,       btn_y, action_w, SAVE_H)
            stop_rect    = pygame.Rect(bx + (action_w + gap) * 2, btn_y, action_w, SAVE_H)
            for r, txt, col, act in [
                (save_rect,    "Save",    (30, 160, 80, 220),   "save"),
                (restart_rect, "Restart", (200, 120, 30, 220),  "system_restart"),
                (stop_rect,    "Stop",    (180, 35, 35, 220),   "system_stop"),
            ]:
                pygame.draw.rect(panel, col, r, border_radius=8)
                s = self._font_label.render(txt, True, (255, 255, 255))
                panel.blit(s, (r.centerx - s.get_width() // 2,
                               r.centery - s.get_height() // 2))
                self._ui_rects.append((r, act, None))
        else:
            save_w    = max(160, W // 5)
            save_rect = pygame.Rect((W - save_w) // 2, btn_y, save_w, SAVE_H)
            pygame.draw.rect(panel, (30, 160, 80, 220), save_rect, border_radius=8)
            sv = self._font_label.render("Save", True, (255, 255, 255))
            panel.blit(sv, (save_rect.centerx - sv.get_width() // 2,
                            save_rect.centery  - sv.get_height() // 2))
            self._ui_rects.append((save_rect, "save", None))

        if _time.monotonic() < self._save_msg_until and self._save_msg:
            col = (100, 255, 150) if "!" in self._save_msg else (255, 140, 100)
            msg = self._font_label.render(self._save_msg, True, col)
            panel.blit(msg, (save_rect.right + 8,
                             save_rect.centery - msg.get_height() // 2))

    # ------------------------------------------------------------------
    # Numpad overlay
    # ------------------------------------------------------------------
    def _draw_numpad(self, panel: "pygame.Surface") -> None:
        W, H = self.width, self.height
        pad = 16

        dim = pygame.Surface((W, H), pygame.SRCALPHA)
        dim.fill((0, 0, 0, 190))
        panel.blit(dim, (0, 0))

        keys = [
            ["7", "8", "9"],
            ["4", "5", "6"],
            ["1", "2", "3"],
            [".", "0", "-"],
        ]
        action_row_labels = ["←", "✓", "✗"]
        action_row_acts   = ["numpad_back", "numpad_confirm", "numpad_cancel"]
        action_row_colors = [(80, 60, 60, 230), (30, 140, 70, 230), (140, 40, 40, 230)]

        btn_size = max(50, min(H // 8, W // 6))
        h_gap, v_gap = pad // 2, pad // 2
        grid_w = btn_size * 3 + h_gap * 2
        grid_h = btn_size * 5 + v_gap * 4
        gx = (W - grid_w) // 2
        # Reserve space for the buffer label above the grid
        label_h = self._font_value.get_height() + pad
        gy = max(label_h, (H - grid_h) // 2)

        field_label = (self._numpad_field_path or "").split(".")[-1].replace("_", " ").title()
        buf_surf = self._font_value.render(
            f"{field_label}: {self._numpad_buffer or '_'}", True, (220, 230, 255)
        )
        panel.blit(buf_surf, (gx, gy - buf_surf.get_height() - pad))

        for row_i, row in enumerate(keys):
            for col_i, key_label in enumerate(row):
                r = pygame.Rect(
                    gx + col_i * (btn_size + h_gap),
                    gy + row_i * (btn_size + v_gap),
                    btn_size, btn_size,
                )
                pygame.draw.rect(panel, (40, 60, 150, 230), r, border_radius=10)
                pygame.draw.rect(panel, (80, 120, 255, 150), r, 2, border_radius=10)
                s = self._font_value.render(key_label, True, (255, 255, 255))
                panel.blit(s, (r.centerx - s.get_width() // 2, r.centery - s.get_height() // 2))
                self._ui_rects.append((r, "numpad_key", key_label))

        action_y = gy + 4 * (btn_size + v_gap)
        for col_i, (act_label, act, color) in enumerate(
            zip(action_row_labels, action_row_acts, action_row_colors)
        ):
            r = pygame.Rect(
                gx + col_i * (btn_size + h_gap),
                action_y,
                btn_size, btn_size,
            )
            pygame.draw.rect(panel, color, r, border_radius=10)
            s = self._font_value.render(act_label, True, (255, 255, 255))
            panel.blit(s, (r.centerx - s.get_width() // 2, r.centery - s.get_height() // 2))
            self._ui_rects.append((r, act, None))

    # ------------------------------------------------------------------
    # OSK helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _find_osk_binary() -> "str | None":
        for name in ("matchbox-keyboard", "onboard", "wvkbd-mobintl", "wvkbd"):
            path = shutil.which(name)
            if path:
                return path
        return None

    def _open_osk(self, path: "str | None", masked: bool, current_value: str,
                  label: str = "", target: str = "field") -> None:
        self._osk_field_path = path
        self._osk_buffer = current_value
        self._osk_masked = masked
        self._osk_shift = False
        self._osk_label = label
        self._osk_target = target

        osk_bin = self._find_osk_binary()
        if osk_bin:
            try:
                self._osk_proc = subprocess.Popen(
                    [osk_bin],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                self._osk_use_subprocess = True
            except Exception:
                logging.warning("OSK launch failed (%s) — using fallback", osk_bin)
                self._osk_use_subprocess = False
        else:
            self._osk_use_subprocess = False

        self._osk_active = True

    def _close_osk(self) -> None:
        if self._osk_proc is not None:
            try:
                self._osk_proc.terminate()
                try:
                    self._osk_proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self._osk_proc.kill()
                    self._osk_proc.wait()
            except Exception:
                pass
            self._osk_proc = None
        self._osk_active = False
        self._osk_use_subprocess = False
        self._osk_field_path = None
        self._osk_buffer = ""
        self._osk_masked = False
        self._osk_shift = False
        self._osk_sym_mode = False
        self._osk_label = ""
        self._osk_target = "field"

    # ------------------------------------------------------------------
    # OSK overlay
    # ------------------------------------------------------------------
    _QWERTY_ROWS  = ["QWERTYUIOP", "ASDFGHJKL", "ZXCVBNM"]
    _OSK_NUM_ROW  = "1234567890"
    _OSK_SYM_NUM  = "!@#$%^&*()"   # shift variant of number row
    _OSK_SYM_ROWS = [               # symbols page — 10 chars per row
        "-_=+[]{}|\\",
        ";:'\",./~`^",
        "<>()@#$%&*",
    ]

    def _draw_osk_overlay(self, panel: "pygame.Surface") -> None:
        W, H = self.width, self.height
        panel.set_clip(None)  # clear any stale clip from settings tab

        GAP   = 4
        # Scale key sizes with screen; keep them usable on small and large displays
        key_h = max(40, min(H // 8, 88))
        key_w = max(44, min(W // 11, 100))
        label_h = self._font_value.get_height()

        n_rows = 4 if not self._osk_sym_mode else 3   # content rows
        ctrl_h = key_h                                  # control row
        grid_h = n_rows * (key_h + GAP) + ctrl_h + GAP * 2

        # Keyboard box anchored to the bottom; input label sits above it
        box_top = H - grid_h - 8
        input_y = box_top - label_h - 12

        # Solid keyboard background (covers any settings content underneath)
        pygame.draw.rect(panel, (8, 12, 32, 255),
                         pygame.Rect(0, input_y - 8, W, H - input_y + 8))
        pygame.draw.line(panel, (60, 100, 255, 200),
                         (0, input_y - 8), (W, input_y - 8), 2)

        # Input display
        display    = ("*" * len(self._osk_buffer)) if self._osk_masked else self._osk_buffer
        field_label = self._osk_label or (
            (self._osk_field_path or "").split(".")[-1].replace("_", " ").title()
        )
        buf_surf = self._font_value.render(
            f"{field_label}: {display or '_'}"[:60], True, (220, 230, 255)
        )
        panel.blit(buf_surf, ((W - buf_surf.get_width()) // 2, input_y))

        if self._osk_use_subprocess:
            hint = self._font_label.render(
                "Type on keyboard  •  Enter = confirm  •  Esc = cancel",
                True, (160, 180, 220),
            )
            panel.blit(hint, ((W - hint.get_width()) // 2, box_top + 20))
            btn_w = 160
            for bx, lbl, act in [
                (W // 2 - btn_w - 10, "Confirm", "osk_confirm"),
                (W // 2 + 10,          "Cancel",  "osk_cancel"),
            ]:
                r = pygame.Rect(bx, box_top + 80, btn_w, key_h)
                col = (30, 140, 70, 230) if act == "osk_confirm" else (140, 40, 40, 230)
                pygame.draw.rect(panel, col, r, border_radius=8)
                s = self._font_label.render(lbl, True, (255, 255, 255))
                panel.blit(s, (r.centerx - s.get_width() // 2, r.centery - s.get_height() // 2))
                self._ui_rects.append((r, act, None))
            return

        def _draw_row(chars: list, y: int, bg: tuple) -> None:
            row_w = len(chars) * (key_w + GAP) - GAP
            rx    = (W - row_w) // 2
            for i, ch in enumerate(chars):
                r = pygame.Rect(rx + i * (key_w + GAP), y, key_w, key_h)
                pygame.draw.rect(panel, bg, r, border_radius=6)
                s = self._font_label.render(ch, True, (255, 255, 255))
                panel.blit(s, (r.centerx - s.get_width() // 2,
                               r.centery - s.get_height() // 2))
                self._ui_rects.append((r, "osk_char", ch))

        y = box_top + GAP
        if self._osk_sym_mode:
            for sym_row in self._OSK_SYM_ROWS:
                _draw_row(list(sym_row), y, (30, 50, 120, 255))
                y += key_h + GAP
        else:
            num_chars = list(self._OSK_SYM_NUM if self._osk_shift else self._OSK_NUM_ROW)
            _draw_row(num_chars, y, (20, 35, 90, 255))   # darker tint = numbers
            y += key_h + GAP
            for row in self._QWERTY_ROWS:
                chars = list(row if self._osk_shift else row.lower())
                _draw_row(chars, y, (40, 60, 150, 255))
                y += key_h + GAP

        # Control row — sized to fit the screen width exactly
        if self._osk_sym_mode:
            specials = [
                ("ABC",   key_w * 2, "osk_sym_toggle"),
                ("Space", key_w * 4, "osk_space"),
                ("←", key_w,    "osk_back"),
                ("OK",    key_w,     "osk_confirm"),
                ("X",     key_w,     "osk_cancel"),
            ]
        else:
            specials = [
                ("Shift", key_w * 2, "osk_shift"),
                ("?123",  key_w * 2, "osk_sym_toggle"),
                ("Space", key_w * 3, "osk_space"),
                ("←", key_w,    "osk_back"),
                ("OK",    key_w,     "osk_confirm"),
                ("X",     key_w,     "osk_cancel"),
            ]
        total_w = sum(w for _, w, _ in specials) + GAP * (len(specials) - 1)
        bx = (W - total_w) // 2
        for lbl, btn_w_sp, act in specials:
            r = pygame.Rect(bx, y, btn_w_sp, key_h)
            colors = {
                "osk_confirm":    (30, 140, 70, 255),
                "osk_cancel":     (140, 40, 40, 255),
                "osk_shift":      (80, 100, 200, 255) if self._osk_shift else (40, 60, 130, 255),
                "osk_sym_toggle": (55, 75, 145, 255),
            }
            pygame.draw.rect(panel, colors.get(act, (50, 70, 160, 255)), r, border_radius=7)
            s = self._font_label.render(lbl, True, (255, 255, 255))
            panel.blit(s, (r.centerx - s.get_width() // 2,
                           r.centery - s.get_height() // 2))
            self._ui_rects.append((r, act, None))
            bx += btn_w_sp + GAP

    # ------------------------------------------------------------------
    # Restart prompt overlay
    # ------------------------------------------------------------------
    def _draw_restart_prompt(self, panel: "pygame.Surface") -> None:
        W, H = self.width, self.height
        overlay = pygame.Surface((W, H), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 180))
        panel.blit(overlay, (0, 0))

        box_w, box_h = min(600, W - 80), 200
        box = pygame.Rect((W - box_w) // 2, (H - box_h) // 2, box_w, box_h)
        pygame.draw.rect(panel, (20, 28, 60, 245), box, border_radius=12)
        pygame.draw.rect(panel, (60, 110, 255, 180), box, 2, border_radius=12)

        msg = self._font_label.render("Some changes need a restart.", True, (220, 225, 255))
        panel.blit(msg, (box.centerx - msg.get_width() // 2, box.y + 28))

        btn_w = box_w // 3
        restart_r = pygame.Rect(box.x + 20,             box.y + 120, btn_w, 52)
        later_r   = pygame.Rect(box.right - btn_w - 20, box.y + 120, btn_w, 52)
        for r, txt, col in [(restart_r, "Restart Now", (30, 160, 80, 220)),
                            (later_r,   "Later",        (80, 80, 120, 220))]:
            pygame.draw.rect(panel, col, r, border_radius=8)
            s = self._font_label.render(txt, True, (255, 255, 255))
            panel.blit(s, (r.centerx - s.get_width() // 2, r.centery - s.get_height() // 2))
        self._ui_rects.append((restart_r, "restart_confirm", None))
        self._ui_rects.append((later_r,   "restart_later",   None))

    def _draw_stop_prompt(self, panel: "pygame.Surface") -> None:
        W, H = self.width, self.height
        overlay = pygame.Surface((W, H), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 180))
        panel.blit(overlay, (0, 0))

        box_w, box_h = min(600, W - 80), 200
        box = pygame.Rect((W - box_w) // 2, (H - box_h) // 2, box_w, box_h)
        pygame.draw.rect(panel, (40, 15, 15, 245), box, border_radius=12)
        pygame.draw.rect(panel, (220, 60, 60, 180), box, 2, border_radius=12)

        msg = self._font_label.render("Stop the container?", True, (255, 180, 180))
        panel.blit(msg, (box.centerx - msg.get_width() // 2, box.y + 28))

        btn_w = box_w // 3
        yes_r = pygame.Rect(box.x + 20,             box.y + 120, btn_w, 52)
        no_r  = pygame.Rect(box.right - btn_w - 20, box.y + 120, btn_w, 52)
        for r, txt, col in [(yes_r, "Stop Now", (180, 35, 35, 220)),
                            (no_r,  "Cancel",   (80, 80, 120, 220))]:
            pygame.draw.rect(panel, col, r, border_radius=8)
            s = self._font_label.render(txt, True, (255, 255, 255))
            panel.blit(s, (r.centerx - s.get_width() // 2, r.centery - s.get_height() // 2))
        self._ui_rects.append((yes_r, "stop_confirm", None))
        self._ui_rects.append((no_r,  "stop_cancel",  None))

    # ------------------------------------------------------------------
    # Event handling (main thread)
    # ------------------------------------------------------------------
    def process_events(self) -> bool:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                return False
            if event.type == pygame.KEYDOWN:
                if self._osk_active:
                    if event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                        self._dispatch("osk_confirm", None)
                    elif event.key == pygame.K_ESCAPE:
                        self._dispatch("osk_cancel", None)
                    elif event.key == pygame.K_BACKSPACE:
                        self._dispatch("osk_back", None)
                    elif event.unicode and event.unicode.isprintable():
                        self._dispatch("osk_char", event.unicode)
                    continue  # swallow all other keys while OSK is open
                if event.key == pygame.K_ESCAPE:
                    if self._panel_visible:
                        self._close_panel()
                    else:
                        return False
                elif event.key == pygame.K_q and not self._panel_visible:
                    return False
            if event.type == pygame.MOUSEBUTTONDOWN:
                if self._panel_visible:
                    self._drag_origin = event.pos
                    self._drag_start = event.pos
                    self._drag_scrolled = False
                else:
                    self._handle_triple_tap()
            if event.type == pygame.MOUSEMOTION and self._panel_visible and self._drag_start is not None:
                dy = event.pos[1] - self._drag_start[1]
                if abs(dy) > 3 and self._active_tab > 0:
                    # Apply incremental scroll for any motion
                    tab = self._active_tab
                    self._scroll_offsets[tab] = max(
                        0, self._scroll_offsets.get(tab, 0) - dy
                    )
                    self._drag_start = event.pos
                    # Only flag as scroll if total displacement from press is large
                    if self._drag_origin is not None:
                        total_dy = abs(event.pos[1] - self._drag_origin[1])
                        if total_dy > 20:
                            self._drag_scrolled = True
            if event.type == pygame.MOUSEBUTTONUP:
                if self._panel_visible and self._drag_origin is not None:
                    if not self._drag_scrolled:
                        self._handle_panel_tap(event.pos)
                    self._drag_origin = None
                    self._drag_start = None
                    self._drag_scrolled = False
            if event.type == pygame.MOUSEWHEEL and self._panel_visible:
                tab = self._active_tab
                if tab > 0:
                    ROW_H = max(46, self.height // 16)
                    self._scroll_offsets[tab] = max(
                        0, self._scroll_offsets.get(tab, 0) - event.y * ROW_H
                    )
            # SDL2 touch events (Wayland doesn't synthesize mouse events from touch)
            if event.type == pygame.FINGERDOWN:
                if self._active_finger_id is None:
                    self._active_finger_id = event.finger_id
                    pos = (int(event.x * self.width), int(event.y * self.height))
                    if self._panel_visible:
                        self._drag_origin = pos
                        self._drag_start = pos
                        self._drag_scrolled = False
                    else:
                        self._handle_triple_tap()
            if event.type == pygame.FINGERMOTION:
                if event.finger_id == self._active_finger_id and self._panel_visible and self._drag_start is not None:
                    pos = (int(event.x * self.width), int(event.y * self.height))
                    dy = pos[1] - self._drag_start[1]
                    if abs(dy) > 3 and self._active_tab > 0:
                        tab = self._active_tab
                        self._scroll_offsets[tab] = max(
                            0, self._scroll_offsets.get(tab, 0) - dy
                        )
                        self._drag_start = pos
                        if self._drag_origin is not None and abs(pos[1] - self._drag_origin[1]) > 20:
                            self._drag_scrolled = True
            if event.type == pygame.FINGERUP:
                if event.finger_id == self._active_finger_id:
                    self._active_finger_id = None
                    if self._panel_visible and self._drag_origin is not None:
                        if not self._drag_scrolled:
                            pos = (int(event.x * self.width), int(event.y * self.height))
                            self._handle_panel_tap(pos)
                        self._drag_origin = None
                        self._drag_start = None
                        self._drag_scrolled = False
                    elif not self._panel_visible:
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

    def _open_panel(self) -> None:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            self._info_url = s.getsockname()[0]
            s.close()
        except Exception:
            self._info_url = "?.?.?.?"

        port = self.settings.get("backend_configs", {}).get("server_port", 80)
        url  = f"http://{self._info_url}" if port == 80 else f"http://{self._info_url}:{port}"
        self._qr_surface = self._make_qr_surface(url)

        try:
            from Utilities.config_store import load_settings
            self._live_settings = load_settings()
        except Exception:
            self._live_settings = dict(self.settings)

        # Build field lists for all settings tabs once
        for tab_idx, sections in _TAB_SECTIONS.items():
            self._tab_fields[tab_idx] = _build_fields(self._live_settings, sections)

        self._pending_changes = {}
        self._scroll_offsets  = {i: 0 for i in range(len(_TABS))}
        self._active_tab      = 0
        self._panel_visible   = True
        # Render immediately so _ui_rects is populated for taps that land
        # in the same event-processing batch that opened the panel.
        self.render_pending_frame()

    def _close_panel(self) -> None:
        self._panel_visible   = False
        self._pending_changes = {}
        self._restart_prompt  = False
        self._stop_prompt     = False
        self._numpad_active   = False
        self._numpad_field_path = None
        self._numpad_buffer   = ""
        self._numpad_field_meta = None
        self._close_osk()
        self._drag_origin = None
        self._drag_start = None
        self._drag_scrolled = False

    # ------------------------------------------------------------------
    # Panel tap dispatch
    # ------------------------------------------------------------------
    def _handle_panel_tap(self, pos: Tuple[int, int]) -> None:
        px, py = pos
        for rect, action, data in self._ui_rects:
            if not rect.collidepoint(px, py):
                continue
            if self._restart_prompt and action not in ("restart_confirm", "restart_later"):
                continue
            if self._stop_prompt and action not in ("stop_confirm", "stop_cancel"):
                continue
            if self._numpad_active and action not in (
                "numpad_key", "numpad_back", "numpad_confirm", "numpad_cancel"
            ):
                continue
            if self._osk_active and action not in (
                "osk_char", "osk_back", "osk_space", "osk_shift",
                "osk_confirm", "osk_cancel", "osk_sym_toggle",
            ):
                continue
            self._dispatch(action, data)
            return

    def _dispatch(self, action: str, data: Any) -> None:
        if action == "close":
            self._close_panel()

        elif action == "tab":
            self._active_tab = int(data)
            # Auto-scroll tab bar so the newly active tab is visible
            TAB_MIN_W = max(70, self.height // 11)
            ARR_W = max(48, self.height // 14)
            avail_w = self.width - ARR_W - 4
            total_tab_w = len(_TABS) * TAB_MIN_W
            if total_tab_w > avail_w:
                vp_w = avail_w - ARR_W * 2
                tab_left = self._active_tab * TAB_MIN_W
                tab_right = tab_left + TAB_MIN_W
                if tab_left < self._tab_scroll_x:
                    self._tab_scroll_x = tab_left
                elif tab_right > self._tab_scroll_x + vp_w:
                    self._tab_scroll_x = tab_right - vp_w

        elif action == "tab_scroll_left":
            TAB_MIN_W = max(70, self.height // 11)
            self._tab_scroll_x = max(0, self._tab_scroll_x - TAB_MIN_W)

        elif action == "tab_scroll_right":
            TAB_MIN_W = max(70, self.height // 11)
            self._tab_scroll_x += TAB_MIN_W  # clamped during drawing

        elif action == "toggle":
            path = data
            merged: dict = {}
            _deep_update(merged, self._live_settings)
            _deep_update(merged, self._pending_changes)
            _set_nested(self._pending_changes, path,
                        not bool(_get_nested(merged, path, False)))

        elif action in ("inc", "dec"):
            path, ftype, step, fmin, fmax = data
            merged: dict = {}
            _deep_update(merged, self._live_settings)
            _deep_update(merged, self._pending_changes)
            try:
                cur = ftype(_get_nested(merged, path, 0))
            except Exception:
                cur = ftype(0)
            new_val = cur + (step if action == "inc" else -step)
            if fmin is not None:
                new_val = max(ftype(fmin), new_val)
            if fmax is not None:
                new_val = min(ftype(fmax), new_val)
            _set_nested(self._pending_changes, path, new_val)

        elif action in ("cycle_next", "cycle_prev"):
            path, choices = data
            merged: dict = {}
            _deep_update(merged, self._live_settings)
            _deep_update(merged, self._pending_changes)
            cur = str(_get_nested(merged, path, choices[0]))
            try:
                idx = choices.index(cur)
            except ValueError:
                idx = 0
            delta = 1 if action == "cycle_next" else -1
            _set_nested(self._pending_changes, path,
                        choices[(idx + delta) % len(choices)])

        elif action == "save":
            self._save_settings()

        elif action == "scroll_up":
            tab = int(data)
            ROW_H = max(46, self.height // 16)
            self._scroll_offsets[tab] = max(0, self._scroll_offsets.get(tab, 0) - ROW_H)

        elif action == "scroll_down":
            tab = int(data)
            ROW_H = max(46, self.height // 16)
            self._scroll_offsets[tab] = self._scroll_offsets.get(tab, 0) + ROW_H

        elif action == "restart_confirm":
            self._restart_prompt = False
            import sys
            python = sys.executable
            os.execl(python, python, *sys.argv)

        elif action == "restart_later":
            self._restart_prompt = False

        elif action == "system_restart":
            import sys
            python = sys.executable
            os.execl(python, python, *sys.argv)

        elif action == "system_stop":
            self._stop_prompt = True

        elif action == "stop_confirm":
            import sys
            sys.exit(0)

        elif action == "stop_cancel":
            self._stop_prompt = False

        elif action == "edit_numeric_string":
            path = data
            merged: dict = {}
            _deep_update(merged, self._live_settings)
            _deep_update(merged, self._pending_changes)
            self._numpad_field_path = path
            self._numpad_buffer = str(_get_nested(merged, path, ""))
            self._numpad_field_meta = None
            self._numpad_active = True

        elif action == "edit_number":
            path, py_type, fmin, fmax = data
            merged: dict = {}
            _deep_update(merged, self._live_settings)
            _deep_update(merged, self._pending_changes)
            try:
                cur = py_type(_get_nested(merged, path, 0))
            except Exception:
                cur = py_type(0)
            self._numpad_field_path = path
            self._numpad_buffer = f"{cur:.2f}" if py_type is float else str(int(cur))
            self._numpad_field_meta = (py_type, fmin, fmax)
            self._numpad_active = True

        elif action == "numpad_key":
            ch = data
            buf = self._numpad_buffer
            if ch == ".":
                if "." not in buf:  # only one decimal point
                    self._numpad_buffer += ch
            elif ch == "-":
                if not buf:  # minus only at position 0
                    self._numpad_buffer += ch
            else:
                self._numpad_buffer += ch

        elif action == "numpad_back":
            self._numpad_buffer = self._numpad_buffer[:-1]

        elif action == "numpad_confirm":
            try:
                raw = float(self._numpad_buffer)  # raises if empty or invalid
            except ValueError:
                pass  # stay open, don't commit
            else:
                if self._numpad_field_path:
                    if self._numpad_field_meta is not None:
                        py_type, fmin, fmax = self._numpad_field_meta
                        new_val = py_type(raw)
                        if fmin is not None:
                            new_val = max(py_type(fmin), new_val)
                        if fmax is not None:
                            new_val = min(py_type(fmax), new_val)
                        commit_val: Any = new_val
                    else:
                        commit_val = self._numpad_buffer  # numeric_string: keep as str
                    parts = self._numpad_field_path.split(".")
                    target = self._pending_changes
                    for p in parts[:-1]:
                        target = target.setdefault(p, {})
                    target[parts[-1]] = commit_val
                self._numpad_active = False
                self._numpad_field_path = None
                self._numpad_buffer = ""
                self._numpad_field_meta = None

        elif action == "numpad_cancel":
            self._numpad_active = False
            self._numpad_field_path = None
            self._numpad_buffer = ""
            self._numpad_field_meta = None

        elif action in ("edit_str", "edit_password"):
            path = data
            merged: dict = {}
            _deep_update(merged, self._live_settings)
            _deep_update(merged, self._pending_changes)
            current_val = str(_get_nested(merged, path, ""))
            self._open_osk(path, masked=(action == "edit_password"), current_value=current_val)

        elif action == "osk_char":
            self._osk_buffer += data

        elif action == "osk_back":
            self._osk_buffer = self._osk_buffer[:-1]

        elif action == "osk_space":
            self._osk_buffer += " "

        elif action == "osk_shift":
            self._osk_shift = not self._osk_shift

        elif action == "osk_sym_toggle":
            self._osk_sym_mode = not self._osk_sym_mode
            self._osk_shift = False

        elif action == "osk_confirm":
            if self._osk_target == "wifi" and self._wifi_selected_ssid:
                self._start_wifi_connect(self._wifi_selected_ssid, self._osk_buffer)
                self._close_osk()
            elif self._osk_field_path:
                parts = self._osk_field_path.split(".")
                target = self._pending_changes
                for p in parts[:-1]:
                    target = target.setdefault(p, {})
                target[parts[-1]] = self._osk_buffer
                self._close_osk()
            else:
                self._close_osk()

        elif action == "osk_cancel":
            self._close_osk()

        elif action == "wifi_scan":
            self._start_wifi_scan()

        elif action == "wifi_select":
            self._wifi_selected_ssid = data

        elif action == "wifi_connect":
            ssid = data
            self._wifi_selected_ssid = ssid
            net = next((n for n in self._wifi_networks if n["ssid"] == ssid), {})
            if net.get("security"):
                self._open_osk(
                    path=None, masked=True, current_value="",
                    label=f"Password for {ssid}", target="wifi",
                )
            else:
                self._start_wifi_connect(ssid, "")

    # ------------------------------------------------------------------
    # WiFi management
    # ------------------------------------------------------------------
    def _start_wifi_scan(self) -> None:
        if self._wifi_scanning:
            return
        self._wifi_scanning = True
        self._wifi_networks = []
        import threading
        threading.Thread(target=self._do_wifi_scan, daemon=True).start()

    @staticmethod
    def _scan_via_nmcli() -> list:
        """Scan using nmcli (requires D-Bus access to host NetworkManager)."""
        result = subprocess.run(
            ["nmcli", "--terse", "--escape", "no",
             "-f", "SSID,SIGNAL,SECURITY",
             "device", "wifi", "list", "--rescan", "yes"],
            capture_output=True, text=True, timeout=25,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "nmcli error")
        networks: list = []
        seen: set = set()
        for line in result.stdout.strip().splitlines():
            # Split from right: SSID can contain colons; SIGNAL and SECURITY cannot.
            parts = line.rsplit(":", 2)
            if len(parts) < 3:
                continue
            ssid, signal_str, security = parts[0].strip(), parts[1].strip(), parts[2].strip()
            if not ssid or ssid in seen:
                continue
            seen.add(ssid)
            try:
                signal = int(signal_str)
            except ValueError:
                signal = 0
            # nmcli uses "--" for open networks
            secured = bool(security and security != "--")
            networks.append({"ssid": ssid, "signal": signal, "security": security if secured else ""})
        return sorted(networks, key=lambda n: -n["signal"])

    @staticmethod
    def _scan_via_iwlist() -> list:
        """Fallback scan using iwlist (wireless-tools, no daemon needed)."""
        import re
        # Find wireless interfaces
        iw_dev = subprocess.run(["iwconfig"], capture_output=True, text=True, timeout=5)
        ifaces = re.findall(r'^(\w+)\s+IEEE 802\.11', iw_dev.stdout, re.MULTILINE)
        if not ifaces:
            # Try /proc/net/wireless as a last resort
            try:
                with open("/proc/net/wireless") as f:
                    for line in f:
                        m = re.match(r'^\s*(\w+):', line)
                        if m:
                            ifaces.append(m.group(1))
            except OSError:
                pass
        if not ifaces:
            raise RuntimeError("No wireless interface found")

        iface = ifaces[0]
        result = subprocess.run(
            ["iwlist", iface, "scan"],
            capture_output=True, text=True, timeout=20,
        )
        networks: list = []
        seen: set = set()
        current: dict = {}
        for line in result.stdout.splitlines():
            line = line.strip()
            m = re.search(r'ESSID:"(.*?)"', line)
            if m:
                ssid = m.group(1)
                if ssid and ssid not in seen:
                    if current.get("ssid"):
                        networks.append(current)
                    seen.add(ssid)
                    current = {"ssid": ssid, "signal": 0, "security": ""}
            m = re.search(r'Quality=(\d+)/(\d+)', line)
            if m and current:
                current["signal"] = int(int(m.group(1)) * 100 / int(m.group(2)))
            if "Encryption key:on" in line and current:
                current["security"] = "WPA"
        if current.get("ssid"):
            networks.append(current)
        return sorted(networks, key=lambda n: -n["signal"])

    def _do_wifi_scan(self) -> None:
        import shutil
        errors = []
        networks: list = []
        try:
            if shutil.which("nmcli"):
                networks = self._scan_via_nmcli()
            else:
                errors.append("nmcli not found")
        except Exception as exc:
            logging.warning("nmcli scan failed: %s", exc)
            errors.append(f"nmcli: {exc}")

        if not networks:
            try:
                if shutil.which("iwlist"):
                    networks = self._scan_via_iwlist()
                else:
                    errors.append("iwlist not found")
            except Exception as exc:
                logging.warning("iwlist scan failed: %s", exc)
                errors.append(f"iwlist: {exc}")

        if networks:
            self._wifi_networks = networks
            self._wifi_msg = f"Found {len(networks)} network(s)"
            self._wifi_msg_until = _time.monotonic() + 3.0
        else:
            msg = "; ".join(errors) if errors else "No networks found"
            logging.error("WiFi scan failed: %s", msg)
            self._wifi_msg = f"Scan failed: {msg[:60]}"
            self._wifi_msg_until = _time.monotonic() + 6.0
        self._wifi_scanning = False

    def _start_wifi_connect(self, ssid: str, password: str) -> None:
        self._wifi_msg = f"Connecting to {ssid}..."
        self._wifi_msg_until = _time.monotonic() + 30.0
        import threading
        threading.Thread(
            target=self._do_wifi_connect, args=(ssid, password), daemon=True
        ).start()

    def _do_wifi_connect(self, ssid: str, password: str) -> None:
        try:
            cmd = ["nmcli", "device", "wifi", "connect", ssid]
            if password:
                cmd += ["password", password]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if result.returncode == 0:
                self._wifi_msg = f"Connected to {ssid}"
            else:
                err = (result.stderr or result.stdout).strip()
                self._wifi_msg = err[:80] if err else "Connection failed"
            self._wifi_msg_until = _time.monotonic() + 8.0
        except Exception as exc:
            self._wifi_msg = f"Error: {exc}"
            self._wifi_msg_until = _time.monotonic() + 5.0

    def _draw_wifi_tab(self, panel: "pygame.Surface", top: int, bottom: int, pad: int) -> None:
        """Full-content-area WiFi tab: scan button top, list middle, connect button bottom."""
        W      = self.width
        ROW_H  = max(46, self.height // 16)
        SCAN_H = max(44, self.height // 14)
        CONN_H = max(48, self.height // 13)

        # Scan button row (fixed at top, not scrollable)
        scan_txt = "Scanning..." if self._wifi_scanning else "Scan for Networks"
        scan_col = (40, 55, 90, 255) if self._wifi_scanning else (40, 100, 220, 255)
        scan_w   = max(220, W // 3)
        scan_r   = pygame.Rect((W - scan_w) // 2, top + pad // 2, scan_w, SCAN_H)
        pygame.draw.rect(panel, scan_col, scan_r, border_radius=10)
        ss = self._font_label.render(scan_txt, True, (255, 255, 255))
        panel.blit(ss, (scan_r.centerx - ss.get_width() // 2,
                        scan_r.centery - ss.get_height() // 2))
        if not self._wifi_scanning:
            self._ui_rects.append((scan_r, "wifi_scan", None))

        # Status message below scan button
        fixed_top = top + SCAN_H + pad
        if self._wifi_msg and _time.monotonic() < self._wifi_msg_until:
            msg_col = (80, 240, 130) if "Connected" in self._wifi_msg else (255, 185, 80)
            ms = self._font_label.render(self._wifi_msg[:80], True, msg_col)
            panel.blit(ms, ((W - ms.get_width()) // 2, fixed_top + 4))
            fixed_top += ms.get_height() + 8

        # ── Connect button (fixed, bottom) ────────────────────────────────
        conn_bottom = bottom - pad // 2
        conn_top    = conn_bottom - CONN_H
        conn_r      = pygame.Rect(pad, conn_top, W - pad * 2, CONN_H)
        has_sel     = bool(self._wifi_selected_ssid)
        conn_col    = (35, 140, 60, 255) if has_sel else (30, 40, 60, 180)
        pygame.draw.rect(panel, conn_col, conn_r, border_radius=10)
        conn_label  = (f"Connect to  {self._wifi_selected_ssid}"
                       if has_sel else "Select a network above")
        cl = self._font_label.render(conn_label[:50], True,
                                     (255, 255, 255) if has_sel else (100, 110, 140))
        panel.blit(cl, (conn_r.centerx - cl.get_width() // 2,
                        conn_r.centery - cl.get_height() // 2))
        if has_sel:
            self._ui_rects.append((conn_r, "wifi_connect", self._wifi_selected_ssid))

        # ── Scrollable network list ───────────────────────────────────────
        content_top    = fixed_top
        content_bottom = conn_top - pad // 2
        avail_h        = max(1, content_bottom - content_top)

        ARR_H     = max(36, self.height // 18)
        ARR_COLOR = (50, 80, 180, 200)

        nets       = self._wifi_networks
        total_h    = len(nets) * (ROW_H + 4) if nets else ROW_H
        max_scroll = max(0, total_h - avail_h + ARR_H * 2)
        scroll     = max(0, min(self._scroll_offsets.get(1, 0), max_scroll))
        self._scroll_offsets[1] = scroll

        panel.set_clip(pygame.Rect(0, content_top, W, avail_h))
        y = content_top - scroll

        if not nets and not self._wifi_scanning:
            es = self._font_label.render(
                "Tap 'Scan for Networks' to discover WiFi", True, (130, 140, 170))
            panel.blit(es, (pad + 8, content_top + (ROW_H - es.get_height()) // 2))
        else:
            for net in nets:
                ssid    = net["ssid"]
                signal  = net["signal"]
                secured = bool(net.get("security"))
                row_y   = y
                y      += ROW_H + 4

                if row_y + ROW_H <= content_top or row_y >= content_bottom:
                    continue

                row_r  = pygame.Rect(pad, row_y, W - pad * 2, ROW_H)
                is_sel = (ssid == self._wifi_selected_ssid)
                bg     = pygame.Surface((row_r.w, row_r.h), pygame.SRCALPHA)
                bg.fill((20, 70, 20, 200) if is_sel else (20, 28, 55, 200))
                panel.blit(bg, row_r.topleft)
                pygame.draw.rect(panel,
                                 (80, 130, 220, 150) if is_sel else (60, 90, 180, 80),
                                 row_r, 2 if is_sel else 1)

                bars = ("▂▄▆█" if signal >= 75 else
                        "▂▄▆ " if signal >= 50 else
                        "▂▄  " if signal >= 25 else "▂   ")
                lock = "\U0001f512 " if secured else "   "
                ls   = self._font_label.render(f"{bars} {lock}{ssid}", True, (210, 225, 245))
                max_label_w = row_r.w - pad
                if ls.get_width() > max_label_w:
                    ls = ls.subsurface((0, 0, max_label_w, ls.get_height()))
                panel.blit(ls, (pad + 10, row_y + (ROW_H - ls.get_height()) // 2))

                self._ui_rects.append((row_r, "wifi_select", ssid))

        panel.set_clip(None)

        # Scroll arrows
        if scroll > 0:
            up_r = pygame.Rect(pad, content_top, W - pad * 2, ARR_H)
            pygame.draw.rect(panel, ARR_COLOR, up_r, border_radius=6)
            up_s = self._font_label.render("▲  scroll up", True, (200, 220, 255))
            panel.blit(up_s, (up_r.centerx - up_s.get_width() // 2,
                               up_r.centery - up_s.get_height() // 2))
            self._ui_rects.append((up_r, "scroll_up", 1))
        if scroll < max_scroll:
            dn_r = pygame.Rect(pad, content_bottom - ARR_H, W - pad * 2, ARR_H)
            pygame.draw.rect(panel, ARR_COLOR, dn_r, border_radius=6)
            dn_s = self._font_label.render("▼  scroll down", True, (200, 220, 255))
            panel.blit(dn_s, (dn_r.centerx - dn_s.get_width() // 2,
                               dn_r.centery - dn_s.get_height() // 2))
            self._ui_rects.append((dn_r, "scroll_down", 1))

    def _save_settings(self) -> None:
        try:
            from Utilities.config_events import notify_settings_changed
            from Utilities.config_store import load_settings, save_settings
            current = load_settings()
            _deep_update(current, self._pending_changes)
            save_settings(current)
            # Fire callbacks immediately — don't wait for watchdog
            notify_settings_changed(current)
            self._live_settings = current
            from Utilities.config_store import get_restart_required_paths
            changed = set(_collect_dotted_keys(self._pending_changes))
            if changed & get_restart_required_paths():
                self._restart_prompt = True
            self._pending_changes = {}
            # Rebuild field lists to reflect saved values
            for tab_idx, sections in _TAB_SECTIONS.items():
                self._tab_fields[tab_idx] = _build_fields(self._live_settings, sections)
            self._save_msg       = "Saved!"
            self._save_msg_until = _time.monotonic() + 3.0
        except Exception as e:
            self._save_msg       = f"Error: {e}"
            self._save_msg_until = _time.monotonic() + 5.0
            logging.error("Settings save error: %s", e)

    # ------------------------------------------------------------------
    # QR code generation
    # ------------------------------------------------------------------
    @staticmethod
    def _make_qr_surface(url: str) -> Optional[pygame.Surface]:
        try:
            import qrcode  # type: ignore[import]
            qr = qrcode.QRCode(
                version=None,
                error_correction=qrcode.constants.ERROR_CORRECT_M,
                box_size=10,
                border=2,
            )
            qr.add_data(url)
            qr.make(fit=True)
            img = qr.make_image(fill_color=(20, 20, 40), back_color=(255, 255, 255))
            rgb = img.convert("RGB")
            w, h = rgb.size
            return pygame.image.frombuffer(rgb.tobytes(), (w, h), "RGB")
        except Exception as e:
            logging.warning("QR code generation failed: %s", e)
            return None

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
