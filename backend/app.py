#!/usr/bin/env python3
"""Digital Photo Frame — entry point."""
from __future__ import annotations

import argparse
import logging
import os
import subprocess

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _disable_wifi_power_save() -> None:
    """
    Best-effort: disable WiFi power management so the Pi stays reachable when
    the screen is off.  Runs silently; failures are never fatal.

    With network_mode: host the container shares the host network namespace, so
    nmcli / iw commands here affect the host's WiFi adapter directly.
    """
    try:
        import glob
        ifaces = [os.path.basename(p) for p in glob.glob("/sys/class/net/wlan*")]
        for iface in ifaces:
            # iw respects CAP_NET_ADMIN (granted via privileged: true) without
            # needing EUID 0, unlike iwconfig which always checks the real UID.
            for cmd in (
                ["iw", "dev", iface, "set", "power_save", "off"],
                ["iwconfig", iface, "power", "off"],  # fallback
            ):
                try:
                    result = subprocess.run(
                        cmd,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.PIPE,
                        timeout=3,
                    )
                    if result.returncode == 0:
                        logging.info("WiFi power-save disabled for %s", iface)
                        break
                except FileNotFoundError:
                    continue
                except Exception:
                    break
    except Exception:
        pass


def _abs_path(p: str) -> str:
    return p if os.path.isabs(p) else os.path.abspath(os.path.join(BASE_DIR, p))


def main() -> None:
    p = argparse.ArgumentParser(description="Digital Photo Frame")
    p.add_argument("--settings", default="photoframe_settings.json",
                   help="Path to settings JSON file.")
    p.add_argument("--headless", action="store_true",
                   help="Run without GUI (backend server only).")
    p.add_argument("--display", choices=["pygame"],
                   help="Display backend (pygame).")
    p.add_argument("--width", type=int, default=None,
                   help="Headless mode: override stream width.")
    p.add_argument("--height", type=int, default=None,
                   help="Headless mode: override stream height.")
    args = p.parse_args()

    _disable_wifi_power_save()

    from Utilities import config_events
    from Utilities.config_store import apply_system_timezone, load_settings
    settings = load_settings(json_path=_abs_path(args.settings))
    apply_system_timezone(settings)
    config_events.on_settings_changed(apply_system_timezone)
    config_events.start_watcher()

    from app_modes import _run_headless, _run_pygame

    if args.headless:
        _run_headless(settings, _abs_path(args.settings), args.width, args.height)
        return

    if args.display == "pygame":
        _run_pygame(settings, _abs_path(args.settings))
        return

    try:
        _run_pygame(settings, _abs_path(args.settings))
    except Exception as exc:
        # pygame import/display init can fail (e.g. no Wayland surface yet).
        # Fall back to headless so the web API stays alive without a display.
        logging.warning("pygame startup failed (%s), falling back to headless mode", exc)
        _run_headless(settings, _abs_path(args.settings), None, None)


if __name__ == "__main__":
    main()
