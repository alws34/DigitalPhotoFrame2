import random as rand

import cv2
import numpy as np

PRESETS = {
    "milk_glass":   {"background_blur_radius": 60, "background_opacity": 0.3, "tint_color": "#ffffff", "tint_opacity": 0.3},
    "tinted_glass": {"background_blur_radius": 30, "background_opacity": 0.5, "tint_color": "#4488ff", "tint_opacity": 0.2},
    "frosted_dark": {"background_blur_radius": 70, "background_opacity": 0.8, "tint_color": "#000000", "tint_opacity": 0.4},
    "clear":        {"background_blur_radius": 10, "background_opacity": 0.15, "tint_color": "#ffffff", "tint_opacity": 0.0},
}

_PRESET_KEYS = frozenset({"background_blur_radius", "background_opacity", "tint_color", "tint_opacity"})


def _hex_to_bgr(hex_color: str):
    """Parse '#rrggbb' → (B, G, R) ints. Returns (0,0,0) on error."""
    try:
        h = hex_color.lstrip("#")
        r = int(h[0:2], 16)
        g = int(h[2:4], 16)
        b = int(h[4:6], 16)
        return b, g, r
    except (ValueError, IndexError):
        return 0, 0, 0


class Image_Utils():
    def __init__(self, settings: dict):
        self.settings = settings

    def _get_effect_val(self, key, default):
        """Read an effects value, honouring preset overrides for blur/opacity/tint keys."""
        eff = self.settings.get('effects', {}) or {}
        preset_name = eff.get('preset', 'custom')
        if preset_name != 'custom' and key in _PRESET_KEYS:
            preset_val = PRESETS.get(preset_name, {}).get(key)
            if preset_val is not None:
                return preset_val
        if key in eff:
            return eff[key]
        if key in self.settings:
            return self.settings[key]
        return default

    def shuffle_images(self, images):
        images_copy = list(images)
        rand.shuffle(images_copy)
        return images_copy

    def create_translucent_background(self, image, target_width, target_height):
        h, w = image.shape[:2]
        aspect_src = w / h
        aspect_target = target_width / target_height

        if aspect_src > aspect_target:
            new_w = int(h * aspect_target)
            offset = (w - new_w) // 2
            crop = image[:, offset:offset + new_w]
        else:
            new_h = int(w / aspect_target)
            offset = (h - new_h) // 2
            crop = image[offset:offset + new_h, :]

        background = cv2.resize(crop, (target_width, target_height))

        # Blur — presets always blur; custom respects background_blur_enabled
        preset_name = (self.settings.get('effects', {}) or {}).get('preset', 'custom')
        blur_enabled = preset_name != 'custom' or self._get_effect_val('background_blur_enabled', True)
        if blur_enabled:
            bg_blur = int(self._get_effect_val('background_blur_radius', 61))
            if bg_blur > 0:
                if bg_blur % 2 == 0:
                    bg_blur += 1
                background = cv2.GaussianBlur(background, (bg_blur, bg_blur), 0)

        # Dim
        bg_opacity = float(self._get_effect_val('background_opacity', 0.4))
        if bg_opacity < 1.0:
            background = (background.astype(np.float32) * bg_opacity).astype(np.uint8)

        # Tint overlay
        tint_color = str(self._get_effect_val('tint_color', '#000000'))
        tint_opacity = float(self._get_effect_val('tint_opacity', 0.0))
        if tint_opacity > 0:
            b, g, r = _hex_to_bgr(tint_color)
            tint = np.array([[[b, g, r]]], dtype=np.float32)
            background = (
                background.astype(np.float32) * (1.0 - tint_opacity) + tint * tint_opacity
            ).clip(0, 255).astype(np.uint8)

        return background

    def _apply_shadow(self, background, x, y, w, h):
        shadow_opacity = float(self._get_effect_val('shadow_opacity', 0.8))
        shadow_blur = int(self._get_effect_val('shadow_blur_radius', 61))

        if shadow_opacity <= 0:
            return background

        if shadow_blur % 2 == 0:
            shadow_blur += 1

        bg_h, bg_w = background.shape[:2]
        shadow_mask = np.zeros((bg_h, bg_w), dtype=np.uint8)
        cv2.rectangle(shadow_mask, (x, y), (x + w, y + h), (255), thickness=-1)
        shadow_mask = cv2.GaussianBlur(shadow_mask, (shadow_blur, shadow_blur), 0)

        norm_mask = shadow_mask.astype(np.float32) / 255.0
        burn_factor = 1.0 - (norm_mask * shadow_opacity)
        burn_factor = np.dstack([burn_factor] * 3)
        return (background.astype(np.float32) * burn_factor).astype(np.uint8)

    def resize_image_with_background(self, image, target_width, target_height, skip_background=False):
        if image is None:
            return np.zeros((target_height, target_width, 3), dtype=np.uint8)

        original_height, original_width = image.shape[:2]
        aspect_ratio = original_width / original_height

        if target_width / target_height > aspect_ratio:
            new_height = target_height
            new_width = int(new_height * aspect_ratio)
        else:
            new_width = target_width
            new_height = int(new_width / aspect_ratio)

        resized_image = cv2.resize(image, (new_width, new_height))

        # Determine background
        allow_translucent = self._get_effect_val('allow_translucent_background', True)
        bg_type = str(self._get_effect_val('background_type', 'blur'))

        if skip_background:
            background = np.zeros((target_height, target_width, 3), dtype=np.uint8)
            has_bg = False
        elif bg_type == 'color':
            color_hex = str(self._get_effect_val('background_color', '#000000'))
            b, g, r = _hex_to_bgr(color_hex)
            background = np.full((target_height, target_width, 3), [b, g, r], dtype=np.uint8)
            has_bg = True
        elif bg_type == 'none' or not allow_translucent:
            background = np.zeros((target_height, target_width, 3), dtype=np.uint8)
            has_bg = False
        else:
            background = self.create_translucent_background(image, target_width, target_height)
            has_bg = True

        y_offset = (target_height - new_height) // 2
        x_offset = (target_width - new_width) // 2

        if has_bg and self._get_effect_val('shadow_enabled', True):
            background = self._apply_shadow(background, x_offset, y_offset, new_width, new_height)

        y1, y2 = max(0, y_offset), min(target_height, y_offset + new_height)
        x1, x2 = max(0, x_offset), min(target_width, x_offset + new_width)
        img_h, img_w = y2 - y1, x2 - x1

        if img_h > 0 and img_w > 0:
            if img_h != resized_image.shape[0] or img_w != resized_image.shape[1]:
                resized_image = cv2.resize(image, (img_w, img_h))
            background[y1:y2, x1:x2] = resized_image

        return background
