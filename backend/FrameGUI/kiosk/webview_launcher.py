#!/usr/bin/env python3
"""Standalone fullscreen kiosk webview for the triple-tap admin UI.

Spawned once by FrameGUI/photoframe_view_pygame.py — a separate GTK3 +
WebKit2 process, not embedded in pygame's SDL surface (the two toolkits
can't share a window). Loads and authenticates immediately, then stays
alive hidden: SIGUSR1 reveals it, the on-screen close button re-hides it
(does not quit), so only the very first launch pays the GTK/WebKit2
cold-start cost. labwc brings the window to front on reveal; pygame keeps
rendering underneath the whole time.

Usage: webview_launcher.py <url>
"""
from __future__ import annotations

import logging
import os
import signal
import sys

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("WebKit2", "4.1")

from gi.repository import Gdk, GLib, Gtk, WebKit2  # noqa: E402

logging.basicConfig(level=logging.INFO, format="[webview_launcher] %(message)s")

# DSI-1 panel logical size (after labwc applies its 270° transform). Confirmed
# via `wlr-randr` + a live Gtk.Monitor.get_geometry() readout on-device — this
# is a fixed single-device kiosk, not worth discovering dynamically.
_SCREEN_WIDTH = 1280
_SCREEN_HEIGHT = 720


def build_window(url: str) -> Gtk.Window:
    window = Gtk.Window()
    window.set_decorated(False)
    # Placement and sizing are owned by labwc's windowRule for this window
    # (identifier "webview_launcher.py" in rc.xml: serverDecoration="no" plus
    # MoveTo/ResizeTo actions to 0,0 / 1280x720), not by GTK API calls here.
    # Both fullscreen() and maximize() were tried and rejected:
    #  - fullscreen() makes labwc stack the window above every layer-shell
    #    layer including "overlay", where squeekboard draws the OSK — the
    #    keyboard becomes invisible even while squeekboard reports itself
    #    visible.
    #  - maximize() (with or without a follow-up resize()) reliably left a
    #    36px gap versus the true 1280x720 output. GTK's own geometry
    #    getters (get_root_origin/get_size) kept reporting the requested
    #    values as if they'd been honored — Wayland has no protocol for a
    #    client to query its real on-screen position, so GTK was just
    #    echoing back what we told it, not compositor truth. The rc.xml
    #    MoveTo/ResizeTo actions run server-side against the same "on first
    #    map" hook as serverDecoration, so they're authoritative instead.
    window.set_default_size(_SCREEN_WIDTH, _SCREEN_HEIGHT)

    webview = WebKit2.WebView()
    settings = webview.get_settings()
    settings.set_enable_developer_extras(False)
    settings.set_enable_page_cache(False)
    # Tried ALWAYS (real GPU, then Mesa llvmpipe software GL) -- both made
    # the display corrupt (scanline garbage over grim screenshots), most
    # likely labwc's own DRM/KMS ownership conflicting with WebKit's V3D
    # context. Reverted to Cairo software rendering, the known-good path.
    settings.set_hardware_acceleration_policy(WebKit2.HardwareAccelerationPolicy.NEVER)
    webview.load_uri(url)

    def hide_window(*_args):
        window.hide()
        # Tell the parent pygame process (which spawned this via
        # subprocess.Popen, so getppid() is it) to resume its own frame
        # rendering -- it pauses that while this window covers the screen,
        # see PhotoFramePygame._kiosk_visible.
        try:
            os.kill(os.getppid(), signal.SIGUSR2)
        except (ProcessLookupError, PermissionError):
            pass
        return True

    close_button = Gtk.Button(label="✕")  # ✕
    close_button.set_size_request(48, 48)
    close_button.get_style_context().add_class("kiosk-close")
    close_button.connect("clicked", hide_window)

    overlay = Gtk.Overlay()
    overlay.add(webview)
    overlay.add_overlay(close_button)
    overlay.set_overlay_pass_through(close_button, False)
    # Bottom-right, not top-right: the React admin UI puts its own Save
    # button in the top-right corner and this would sit on top of it.
    close_button.set_halign(Gtk.Align.END)
    close_button.set_valign(Gtk.Align.END)
    close_button.set_margin_bottom(12)
    close_button.set_margin_end(12)

    css = Gtk.CssProvider()
    css.load_from_data(
        b".kiosk-close { background: rgba(0,0,0,0.55); color: white; "
        b"border-radius: 24px; font-size: 18px; border: none; }"
    )
    Gtk.StyleContext.add_provider_for_screen(
        window.get_screen(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
    )

    window.add(overlay)
    # Closing (via WM or otherwise) hides rather than destroys — the process
    # stays alive, pre-warmed, for the next reveal. Returning True stops the
    # default handler from destroying the window.
    window.connect("delete-event", hide_window)

    # No app-side touch handling at all: coordinate orientation is fixed once,
    # at the host level, via the libinput calibration matrix in
    # /etc/udev/hwdb.d/99-touchscreen-calibration.hwdb, and squeekboard shows
    # /hides itself automatically via the Wayland text-input protocol on
    # focus/blur -- both match the Raspberry Pi docs' own guidance. An
    # earlier "force-hide OSK on outside tap" safety net lived here and was
    # removed: it fired on taps on the *next* input field too (most fields
    # sit in the upper part of the screen), racing squeekboard's own
    # focus-driven show and killing the keyboard instead of letting it
    # reopen for the new field.

    return window


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: webview_launcher.py <url>", file=sys.stderr)
        sys.exit(1)
    url = sys.argv[1]
    window = build_window(url)

    # Stay hidden until revealed — pre-warms the process (interpreter start,
    # gi imports, WebKit2 init, page load/auth) without paying for a visible
    # cold-start on the triple-tap that actually matters to the user.
    def on_reveal(*_args):
        window.show_all()
        window.present()
        GLib.timeout_add(300, _log_geometry_once)
        logging.info("revealed")
        return GLib.SOURCE_CONTINUE

    def _log_geometry_once():
        gdk_win = window.get_window()
        wx, wy = gdk_win.get_root_origin() if gdk_win else (-1, -1)
        ww, wh = window.get_size()
        alloc = window.get_allocation()
        display = Gdk.Display.get_default()
        monitor = display.get_monitor(0) if display else None
        mgeo = monitor.get_geometry() if monitor else None
        logging.info(
            "geometry: window_pos=(%d,%d) window_size=%dx%d alloc=%dx%d+%d+%d "
            "monitor0=%s state=%s",
            wx, wy, ww, wh, alloc.width, alloc.height, alloc.x, alloc.y,
            (mgeo.width, mgeo.height, mgeo.x, mgeo.y) if mgeo else None,
            gdk_win.get_state() if gdk_win else None,
        )
        return GLib.SOURCE_REMOVE

    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGUSR1, on_reveal)

    logging.info("pre-warmed, waiting for SIGUSR1 (pid=%d)", os.getpid())
    Gtk.main()


if __name__ == "__main__":
    main()
