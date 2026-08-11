"""Unit tests for Utilities/brightness.py.

Key regression guard: set_screen_power must use wlr-randr --brightness, NOT --off/--on.
Using --off disconnects the Wayland compositor output, sending a QUIT event to the
pygame window, which stops the render loop and kills the MJPEG stream.
"""
import subprocess
from unittest.mock import MagicMock, patch

import Utilities.brightness as bri

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _proc(returncode=0, stdout="", stderr=""):
    p = MagicMock()
    p.returncode = returncode
    p.stdout = stdout
    p.stderr = stderr
    return p


# ---------------------------------------------------------------------------
# _detect_wlr_output — parser correctness
# ---------------------------------------------------------------------------

_WLR_RANDR_NORMAL = """\
DSI-1 "(null) (null) (DSI-1)"
  Make: (null)
  Model: (null)
  Serial: (null)
  Enabled: yes
  Modes:
    800x480 px, 60.000000 Hz (preferred, current)
NOOP-1 "Headless output 3"
  Enabled: yes
"""

_WLR_RANDR_DSI_OFF = """\
NOOP-1 "Headless output 3"
  Enabled: yes
"""


def test_detect_wlr_output_normal(tmp_path):
    """DSI-1 found and returned; NOOP-1 ignored."""
    bri._wlr_output_name = None
    with (
        patch("Utilities.brightness._which", return_value="/usr/bin/wlr-randr"),
        patch("subprocess.check_output", return_value=_WLR_RANDR_NORMAL),
    ):
        name = bri._detect_wlr_output()
    assert name == "DSI-1"


def test_detect_wlr_output_noop_filtered():
    """NOOP-1 must never be returned as the real output."""
    bri._wlr_output_name = None
    with (
        patch("Utilities.brightness._which", return_value="/usr/bin/wlr-randr"),
        patch("subprocess.check_output", return_value=_WLR_RANDR_DSI_OFF),
    ):
        with patch("Utilities.brightness._load_persisted_output_name", return_value="DSI-1"):
            name = bri._detect_wlr_output()
    # Should fall back to persisted name when no real output is enabled
    assert name in (None, "DSI-1")
    assert name != "NOOP-1"


def test_detect_wlr_output_enabled_property_not_confused_for_output_name():
    """'Enabled:' is a property line (indented) — must NOT become output name."""
    bri._wlr_output_name = None
    raw = "  Enabled: yes\nDSI-1 \"desc\"\n  Enabled: yes\n"
    with (
        patch("Utilities.brightness._which", return_value="/usr/bin/wlr-randr"),
        patch("subprocess.check_output", return_value=raw),
    ):
        name = bri._detect_wlr_output()
    assert name == "DSI-1"


# ---------------------------------------------------------------------------
# set_screen_power — must use --brightness, NEVER --off/--on
# ---------------------------------------------------------------------------

def _mock_wayland_env(monkeypatch):
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")


def _wlr_only_which(name):
    """Only wlr-randr resolves; other tools (vcgencmd, xset) are missing."""
    if "wlr-randr" in name:
        return "/usr/bin/wlr-randr"
    return None


def _brightness_ctx(monkeypatch, fake_run_fn=None):
    """Context manager stack that suppresses _detect_wlr_output's check_output call."""
    from contextlib import ExitStack
    stack = ExitStack()
    # Suppress the check_output inside _detect_wlr_output so it doesn't pollute calls.
    stack.enter_context(patch("subprocess.check_output", return_value=""))
    stack.enter_context(patch("glob.glob", return_value=[]))
    stack.enter_context(patch("Utilities.brightness._which", side_effect=_wlr_only_which))
    if fake_run_fn is not None:
        stack.enter_context(patch("subprocess.run", side_effect=fake_run_fn))
    else:
        stack.enter_context(patch("subprocess.run", return_value=_proc()))
    return stack


def test_set_screen_power_off_uses_brightness_not_off(monkeypatch):
    """--off must NOT appear in wlr-randr call when turning screen off."""
    _mock_wayland_env(monkeypatch)
    bri._wlr_output_name = "DSI-1"
    calls_seen = []

    def fake_run(cmd, **kwargs):
        calls_seen.append(list(cmd))
        return _proc()

    with _brightness_ctx(monkeypatch, fake_run):
        bri.set_screen_power(False)

    # Only look at control calls (have --output), not detection calls
    ctrl_calls = [c for c in calls_seen if "--output" in c]
    assert ctrl_calls, "no wlr-randr control call was made"
    for cmd in ctrl_calls:
        assert "--off" not in cmd, "wlr-randr --off must not be used (breaks pygame)"
        assert "--brightness" in cmd, "expected --brightness to dim the output"


def test_set_screen_power_on_uses_brightness_not_on(monkeypatch):
    """--on must NOT appear in wlr-randr call when turning screen on."""
    _mock_wayland_env(monkeypatch)
    bri._wlr_output_name = "DSI-1"
    calls_seen = []

    def fake_run(cmd, **kwargs):
        calls_seen.append(list(cmd))
        return _proc()

    with _brightness_ctx(monkeypatch, fake_run):
        bri.set_screen_power(True)

    ctrl_calls = [c for c in calls_seen if "--output" in c]
    assert ctrl_calls, "no wlr-randr control call was made"
    for cmd in ctrl_calls:
        assert "--on" not in cmd, "wlr-randr --on must not be used"
        assert "--brightness" in cmd


def test_set_screen_power_off_sends_zero_brightness(monkeypatch):
    """Screen off → --brightness 0.00."""
    _mock_wayland_env(monkeypatch)
    bri._wlr_output_name = "DSI-1"
    captured = []

    def fake_run(cmd, **kwargs):
        captured.append(list(cmd))
        return _proc()

    with _brightness_ctx(monkeypatch, fake_run):
        bri.set_screen_power(False)

    ctrl_calls = [c for c in captured if "--output" in c]
    assert ctrl_calls
    cmd = ctrl_calls[0]
    bri_idx = cmd.index("--brightness")
    assert cmd[bri_idx + 1] == "0.00"


def test_set_screen_power_on_sends_full_brightness(monkeypatch):
    """Screen on → --brightness 1.00."""
    _mock_wayland_env(monkeypatch)
    bri._wlr_output_name = "DSI-1"
    captured = []

    def fake_run(cmd, **kwargs):
        captured.append(list(cmd))
        return _proc()

    with _brightness_ctx(monkeypatch, fake_run):
        bri.set_screen_power(True)

    ctrl_calls = [c for c in captured if "--output" in c]
    assert ctrl_calls
    cmd = ctrl_calls[0]
    bri_idx = cmd.index("--brightness")
    assert cmd[bri_idx + 1] == "1.00"


def test_set_screen_power_returns_ok_true_on_success(monkeypatch):
    _mock_wayland_env(monkeypatch)
    bri._wlr_output_name = "DSI-1"

    with _brightness_ctx(monkeypatch):
        ok, err = bri.set_screen_power(False)

    assert ok is True
    assert err == ""


def test_set_screen_power_falls_through_on_wlr_error(monkeypatch):
    """If wlr-randr fails, it should fall through (return False, not raise)."""
    _mock_wayland_env(monkeypatch)
    bri._wlr_output_name = "DSI-1"

    def fail_run(cmd, **kwargs):
        if "--output" in list(cmd):
            raise subprocess.CalledProcessError(1, cmd, stderr="err")
        return _proc()

    with _brightness_ctx(monkeypatch, fail_run):
        ok, err = bri.set_screen_power(False)

    assert ok is False
    assert err


# ---------------------------------------------------------------------------
# set_brightness_percent
# ---------------------------------------------------------------------------


def test_set_brightness_clamps_minimum(monkeypatch):
    """Brightness is floor-clamped to 10% (never 0 which would be black)."""
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    bri._wlr_output_name = "DSI-1"
    captured = []

    def fake_run(cmd, **kwargs):
        captured.append(cmd)
        return _proc()

    with (
        patch("Utilities.brightness._which", return_value="/usr/bin/wlr-randr"),
        patch("glob.glob", return_value=[]),
        patch("subprocess.run", side_effect=fake_run),
    ):
        bri.set_brightness_percent(0)

    wlr_calls = [c for c in captured if "--brightness" in str(c)]
    assert wlr_calls
    cmd = wlr_calls[0]
    bri_val = float(cmd[cmd.index("--brightness") + 1])
    assert bri_val >= 0.10, "Brightness must not go below 10% (use set_screen_power for off)"
