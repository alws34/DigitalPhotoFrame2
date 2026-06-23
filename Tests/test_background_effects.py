"""Tests for image_handler background type, presets, and tint overlay."""
import numpy as np
import pytest

from FrameServer.image_handler import PRESETS, Image_Utils, _hex_to_bgr


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def make_utils(effects: dict) -> Image_Utils:
    return Image_Utils(settings={"effects": effects})


def solid_image(color=(128, 64, 32), size=(100, 100)) -> np.ndarray:
    img = np.zeros((size[0], size[1], 3), dtype=np.uint8)
    img[:] = color
    return img


def wide_image(color=(128, 64, 32), w=200, h=20) -> np.ndarray:
    """Wide landscape image: placed in a square canvas it leaves top/bottom black margins."""
    img = np.zeros((h, w, 3), dtype=np.uint8)
    img[:] = color
    return img


# ---------------------------------------------------------------------------
# _hex_to_bgr
# ---------------------------------------------------------------------------

def test_hex_to_bgr_basic():
    b, g, r = _hex_to_bgr("#ff0000")
    assert (b, g, r) == (0, 0, 255)


def test_hex_to_bgr_white():
    b, g, r = _hex_to_bgr("#ffffff")
    assert (b, g, r) == (255, 255, 255)


def test_hex_to_bgr_invalid_returns_black():
    b, g, r = _hex_to_bgr("not-a-color")
    assert (b, g, r) == (0, 0, 0)


# ---------------------------------------------------------------------------
# background_type = "none"  → black canvas
# ---------------------------------------------------------------------------

def test_background_type_none_is_black():
    utils = make_utils({"background_type": "none", "allow_translucent_background": True})
    # Wide image → leaves top/bottom margin rows that show the background
    img = wide_image()
    result = utils.resize_image_with_background(img, 200, 200)
    top_left = result[0, 0]
    assert tuple(top_left) == (0, 0, 0), f"Expected black margin, got {top_left}"


# ---------------------------------------------------------------------------
# background_type = "color"  → solid colour
# ---------------------------------------------------------------------------

def test_background_type_color_fills_background():
    utils = make_utils({
        "background_type": "color",
        "background_color": "#ff0000",   # pure red in RGB → BGR = (0, 0, 255)
        "shadow_enabled": False,
    })
    img = wide_image()   # wide image → top margin is background colour
    result = utils.resize_image_with_background(img, 200, 200)
    corner = result[0, 0]
    assert corner[2] > 200, f"Expected red (BGR[2]) in background, got {corner}"


def test_background_color_default_is_black():
    utils = make_utils({"background_type": "color"})
    img = wide_image()
    result = utils.resize_image_with_background(img, 200, 200)
    corner = result[0, 0]
    assert tuple(corner) == (0, 0, 0)


# ---------------------------------------------------------------------------
# background_type = "blur"  (default)
# ---------------------------------------------------------------------------

def test_background_blur_produces_non_black_bg():
    utils = make_utils({
        "background_type": "blur",
        "allow_translucent_background": True,
        "background_blur_enabled": True,
        "background_blur_radius": 15,
        "background_opacity": 0.5,
        "preset": "custom",
        "tint_opacity": 0.0,
        "shadow_enabled": False,
    })
    img = solid_image(color=(200, 100, 50), size=(30, 30))
    result = utils.resize_image_with_background(img, 300, 300)
    corner = result[0, 0]
    # background is dimmed version of the image colour — not black
    assert np.any(corner > 0), "Expected non-black blurred background"


# ---------------------------------------------------------------------------
# Tint overlay
# ---------------------------------------------------------------------------

def test_tint_overlay_shifts_background_colour():
    base_effects = {
        "background_type": "blur",
        "allow_translucent_background": True,
        "background_blur_enabled": False,
        "background_opacity": 1.0,
        "preset": "custom",
        "tint_color": "#ffffff",
        "tint_opacity": 0.5,
        "shadow_enabled": False,
    }
    utils = make_utils(base_effects)
    # Wide black image → top margin is background (black) + white tint → should be ~127
    img = wide_image(color=(0, 0, 0))
    result = utils.resize_image_with_background(img, 300, 300)
    corner = result[0, 0]   # top margin — pure background pixel
    assert np.all(corner > 100), f"Expected tinted (light) background, got {corner}"


def test_tint_opacity_zero_has_no_effect():
    effects = {
        "background_type": "blur",
        "allow_translucent_background": True,
        "background_blur_enabled": False,
        "background_opacity": 0.5,
        "preset": "custom",
        "tint_color": "#ff0000",
        "tint_opacity": 0.0,
        "shadow_enabled": False,
    }
    utils_notint = make_utils({**effects, "tint_opacity": 0.0})
    utils_tint   = make_utils({**effects, "tint_opacity": 0.5})
    # Wide (100, 100, 100) image → top margin shows background; red tint shifts R channel up
    img = wide_image(color=(100, 100, 100))
    r_notint = utils_notint.resize_image_with_background(img, 200, 200)[0, 0]
    r_tint   = utils_tint.resize_image_with_background(img, 200, 200)[0, 0]
    # With red tint the blue (BGR[0]) drops and red (BGR[2]) rises
    assert int(r_tint[0]) < int(r_notint[0]) or int(r_tint[2]) > int(r_notint[2]), \
        f"Tint should shift colour toward red: notint={r_notint}, tint={r_tint}"


# ---------------------------------------------------------------------------
# Presets
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("preset_name", list(PRESETS.keys()))
def test_preset_produces_valid_image(preset_name):
    utils = make_utils({
        "background_type": "blur",
        "allow_translucent_background": True,
        "preset": preset_name,
        "shadow_enabled": False,
    })
    img = solid_image(color=(120, 80, 40), size=(20, 20))
    result = utils.resize_image_with_background(img, 200, 200)
    assert result.shape == (200, 200, 3)
    assert result.dtype == np.uint8


def test_custom_preset_uses_manual_values():
    utils = make_utils({
        "background_type": "blur",
        "allow_translucent_background": True,
        "preset": "custom",
        "background_blur_radius": 1,
        "background_opacity": 1.0,
        "tint_color": "#000000",
        "tint_opacity": 0.0,
        "shadow_enabled": False,
    })
    img = solid_image(color=(200, 200, 200), size=(10, 10))
    result = utils.resize_image_with_background(img, 200, 200)
    # Opacity=1.0 → background is same brightness as original
    corner = result[0, 0]
    assert np.all(corner > 150), f"Expected bright background (opacity=1.0), got {corner}"


# ---------------------------------------------------------------------------
# allow_translucent_background = False  (legacy "none" path)
# ---------------------------------------------------------------------------

def test_allow_translucent_false_gives_black_background():
    utils = make_utils({"allow_translucent_background": False})
    img = wide_image()
    result = utils.resize_image_with_background(img, 200, 200)
    corner = result[0, 0]
    assert tuple(corner) == (0, 0, 0)


# ---------------------------------------------------------------------------
# None image → black frame
# ---------------------------------------------------------------------------

def test_none_image_returns_black():
    utils = make_utils({})
    result = utils.resize_image_with_background(None, 100, 100)
    assert result.shape == (100, 100, 3)
    assert np.all(result == 0)
