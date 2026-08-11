# utils/brightness.py
import glob
import os
import subprocess

# Cache the last known Wayland output name so we can restore it even when
# the output is powered off (wlr-randr won't list it as "enabled" then).
_wlr_output_name: str | None = None

# Persist the output name here so it survives container restarts.
_WLR_NAME_FILE = "/data/wlr-output-name"

# Common Raspberry Pi Wayland output names to try when cache and file are empty.
_WLR_CANDIDATE_NAMES = ["DSI-1", "DSI-2", "HDMI-A-1", "HDMI-A-2", "eDP-1", "LVDS-1"]


def _which(name: str) -> str | None:
    try:
        import shutil
        return shutil.which(name)
    except Exception:
        return None

def _load_persisted_output_name() -> str | None:
    """Read the last-known output name from the persistent file (survives restarts)."""
    try:
        with open(_WLR_NAME_FILE) as f:
            name = f.read().strip()
        return name or None
    except Exception:
        return None

def _persist_output_name(name: str) -> None:
    """Write the output name to disk so it survives a container restart."""
    try:
        with open(_WLR_NAME_FILE, "w") as f:
            f.write(name)
    except Exception:
        pass

def _detect_wlr_output() -> str | None:
    """
    Return the best real Wayland output name, caching + persisting the result.

    wlr-randr format (labwc/Pi):
        DSI-1 "(null) (null) (DSI-1)"    ← output name line, NOT indented
          Make: (null)                    ← property line, indented
          Enabled: yes                    ← indented; "Enabled:" is a property key

    We only treat non-indented lines as output names.  Indented lines are
    properties.  NOOP / headless virtual outputs are skipped.
    """
    global _wlr_output_name
    exe = _which("wlr-randr")
    if not exe:
        return _wlr_output_name or _load_persisted_output_name()
    try:
        raw = subprocess.check_output([exe], text=True, stderr=subprocess.DEVNULL, timeout=3)
    except Exception:
        return _wlr_output_name or _load_persisted_output_name()

    # Parse: track current output block; property lines are indented.
    best: str | None = None          # first non-headless output with Enabled: yes
    first_real: str | None = None    # first non-headless output regardless of enabled state
    cur_out: str | None = None
    cur_enabled = False

    for line in raw.splitlines():
        if not line:
            continue
        is_property = line[0] in (" ", "\t")
        stripped = line.strip()
        if not stripped:
            continue

        if not is_property:
            # Output name line: "NOOP-1 "description"" or "DSI-1 "...""
            cur_out = stripped.split()[0]
            cur_enabled = False
            # Skip virtual headless outputs
            if cur_out.upper().startswith("NOOP") or "headless" in stripped.lower():
                cur_out = None
        elif cur_out is not None and stripped.lower().startswith("enabled:"):
            cur_enabled = stripped.split(":", 1)[1].strip().lower() == "yes"
            if first_real is None:
                first_real = cur_out
            if cur_enabled and best is None:
                best = cur_out

    found = best or first_real
    if found:
        _wlr_output_name = found
        _persist_output_name(found)
    return _wlr_output_name or _load_persisted_output_name()

def _set_wlr_brightness(percent: int) -> tuple[bool, str]:
    exe = _which("wlr-randr")
    if not exe:
        return False, "wlr-randr not found"
    out_name = _detect_wlr_output()
    if not out_name:
        return False, "no Wayland output found"
    # clamp 10..100 to 0.10..1.00, never 0
    pct = max(10, min(100, int(percent)))
    bri = max(0.10, min(1.00, pct / 100.0))
    try:
        subprocess.run([exe, "--output", out_name, "--brightness", f"{bri:.2f}"],
                       check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=3)
        return True, ""
    except subprocess.CalledProcessError as e:
        return False, (e.stderr or e.stdout or "wlr-randr failed").strip()
    except Exception as e:
        return False, str(e)

def _set_sysfs_brightness(percent: int) -> tuple[bool, str]:
    """Write /sys/class/backlight/*/brightness. Requires write permission."""
    try:
        dirs = sorted(glob.glob("/sys/class/backlight/*"))
        if not dirs:
            return False, "no /sys/class/backlight device"
        path = dirs[0]
        with open(os.path.join(path, "max_brightness")) as f:
            maxb = int(f.read().strip())
        target = max(1, min(maxb, int(round(maxb * (max(10, min(100, int(percent))) / 100.0)))))
        try:
            with open(os.path.join(path, "brightness"), "w") as f:
                f.write(str(target))
            return True, ""
        except PermissionError:
            return False, "permission denied writing /sys/class/backlight; add udev rule or use Wayland path"
    except Exception as e:
        return False, str(e)

def _set_xrandr_brightness(percent: int) -> tuple[bool, str]:
    exe = _which("xrandr")
    if not exe:
        return False, "xrandr not found"
    try:
        out = subprocess.check_output([exe, "--verbose"], text=True, stderr=subprocess.DEVNULL, timeout=3)
    except Exception as e:
        return False, str(e)
    name = None
    for line in out.splitlines():
        if line and not line.startswith(" "):
            name = line.split()[0]
            break
    if not name:
        return False, "no X output"
    bri = max(0.10, min(1.00, (max(10, min(100, int(percent)))) / 100.0))
    try:
        subprocess.run([exe, "--output", name, "--brightness", f"{bri:.2f}"],
                       check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=3)
        return True, ""
    except subprocess.CalledProcessError as e:
        return False, (e.stderr or e.stdout or "xrandr failed").strip()
    except Exception as e:
        return False, str(e)

def set_brightness_percent(percent: int) -> tuple[bool, str]:
    """
    Public API used by SettingsViewModel.on_apply_brightness.
    Returns (ok, error_message). Never shows any GUI dialogs.
    For percent == 0, prefer calling set_screen_power(False) to cut the backlight.
    """
    if os.environ.get("WAYLAND_DISPLAY"):
        ok, err = _set_wlr_brightness(percent)
        if ok:
            return True, ""
        # fall through
    # Try sysfs (works for DSI panels; needs write permission)
    ok, err = _set_sysfs_brightness(percent)
    if ok:
        return True, ""
    # Finally, try X11 if running under X
    if os.environ.get("DISPLAY"):
        ok2, err2 = _set_xrandr_brightness(percent)
        if ok2:
            return True, ""
        return False, f"{err}; {err2}"
    return False, err


def set_screen_power(on: bool) -> tuple[bool, str]:
    """
    Physically power the display output on or off (actual backlight cut, not just dim).

    Priority:
      1. sysfs bl_power  — Pi DSI/LVDS backlight (most direct)
      2. wlr-randr       — Wayland compositor output --on/--off
      3. vcgencmd        — Pi firmware HDMI/display power
      4. xset dpms       — X11 DPMS force on/off

    Returns (ok, error_message).
    """
    errors: list[str] = []

    # 1. sysfs bl_power (Pi touchscreen / LVDS backlight).
    #    bl_power: 0 = backlight on (normal), 1 = backlight cut.
    dirs = sorted(glob.glob("/sys/class/backlight/*"))
    if dirs:
        bl_power_path = os.path.join(dirs[0], "bl_power")
        if os.path.exists(bl_power_path):
            val = "0" if on else "1"
            try:
                with open(bl_power_path, "w") as f:
                    f.write(val)
                return True, ""
            except PermissionError:
                try:
                    subprocess.run(
                        f"echo {val} | sudo tee {bl_power_path}",
                        shell=True, check=True,
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    )
                    return True, ""
                except Exception as e:
                    errors.append(f"bl_power sudo: {e}")
            except Exception as e:
                errors.append(f"bl_power: {e}")

    # 2. wlr-randr (Wayland) — dim brightness to 0 / restore to 100.
    #    We deliberately avoid --off/--on because those disconnect the compositor
    #    output entirely, sending a QUIT event to any SDL2/pygame window and
    #    killing the frame-server render loop (which kills the MJPEG stream).
    if os.environ.get("WAYLAND_DISPLAY"):
        exe = _which("wlr-randr")
        if not exe:
            errors.append("wlr-randr not found")
        else:
            out_name = _detect_wlr_output()
            if out_name:
                bri = "0.00" if not on else "1.00"
                try:
                    subprocess.run(
                        [exe, "--output", out_name, "--brightness", bri],
                        check=True, timeout=3,
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                    )
                    return True, ""
                except subprocess.CalledProcessError as e:
                    errors.append(f"wlr-randr --brightness {bri}: {(e.stderr or e.stdout or '').strip()}")
                except Exception as e:
                    errors.append(f"wlr-randr: {e}")
            else:
                errors.append("no Wayland output detected")

    # 3. vcgencmd (Raspberry Pi firmware — HDMI / DSI power rail).
    vcg = _which("vcgencmd")
    if vcg:
        try:
            subprocess.run(
                [vcg, "display_power", "1" if on else "0"],
                check=True, timeout=3,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            return True, ""
        except Exception as e:
            errors.append(f"vcgencmd: {e}")

    # 4. xset DPMS (X11).
    if os.environ.get("DISPLAY"):
        xset = _which("xset")
        if xset:
            try:
                subprocess.run(
                    [xset, "dpms", "force", "on" if on else "off"],
                    check=True, timeout=3,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                )
                return True, ""
            except Exception as e:
                errors.append(f"xset dpms: {e}")

    return False, "; ".join(errors) or "no display power control method found"


def get_brightness_percent() -> int | None:
    """
    Return the current brightness as an integer percentage (0–100), or None if unreadable.
    Tries sysfs first, then xrandr (X11), then wlr-randr (Wayland).
    """
    # sysfs backlight (works for DSI/LVDS panels on Pi and laptops)
    try:
        dirs = sorted(glob.glob("/sys/class/backlight/*"))
        if dirs:
            path = dirs[0]
            with open(os.path.join(path, "max_brightness")) as f:
                maxb = int(f.read().strip())
            with open(os.path.join(path, "brightness")) as f:
                cur = int(f.read().strip())
            if maxb > 0:
                return round(cur / maxb * 100)
    except Exception:
        pass

    # xrandr (X11)
    if os.environ.get("DISPLAY"):
        try:
            exe = _which("xrandr")
            if exe:
                out = subprocess.check_output(
                    [exe, "--verbose"], text=True, stderr=subprocess.DEVNULL, timeout=3
                )
                for line in out.splitlines():
                    stripped = line.strip().lower()
                    if stripped.startswith("brightness:"):
                        val = float(stripped.split(":")[1].strip())
                        return round(val * 100)
        except Exception:
            pass

    # wlr-randr (Wayland)
    if os.environ.get("WAYLAND_DISPLAY"):
        try:
            exe = _which("wlr-randr")
            if exe:
                out = subprocess.check_output(
                    [exe], text=True, stderr=subprocess.DEVNULL, timeout=3
                )
                for line in out.splitlines():
                    stripped = line.strip().lower()
                    if "brightness:" in stripped:
                        val = float(stripped.split("brightness:")[1].strip().split()[0])
                        return round(val * 100)
        except Exception:
            pass

    return None
