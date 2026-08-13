import time
from typing import Dict, Tuple

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont


class OverlayRenderer:
    def __init__(
        self,
        font_path: str,
        time_font_size: int,
        date_font_size: int,
        stats_font_size: int,
        desired_size: Tuple[int, int],
        enable_panels: bool = False,
        panel_alpha: int = 128,     # 0..255
        panel_padding: int = 12,    # px around text
        panel_radius: int = 10,     # rounded corners
    ) -> None:
        self.desired_w, self.desired_h = desired_size
        self.time_font = ImageFont.truetype(font_path, time_font_size)
        self.date_font = ImageFont.truetype(font_path, date_font_size)
        self.stats_font = ImageFont.truetype(font_path, stats_font_size)

        # Panel controls
        self.enable_panels = bool(enable_panels)
        self.panel_alpha = max(0, min(255, int(panel_alpha)))
        self.panel_padding = max(0, int(panel_padding))
        self.panel_radius = max(0, int(panel_radius))

        # Cache sizes for layout
        self.time_font_size = time_font_size
        self.date_font_size = date_font_size

    @staticmethod
    def _color_from_name(name: str) -> Tuple[int, int, int]:
        cmap = {
            "yellow": (255, 255, 0),
            "white": (255, 255, 255),
            "red": (255, 0, 0),
            "green": (0, 255, 0),
            "blue": (0, 0, 255),
        }
        return cmap.get((name or "").lower(), (255, 255, 255))

    @staticmethod
    def resize_and_crop(src_bgr: np.ndarray, target_w: int, target_h: int) -> np.ndarray:
        h, w = src_bgr.shape[:2]
        aspect_src = w / float(h)
        aspect_dst = target_w / float(target_h)

        if aspect_src > aspect_dst:
            # crop width
            new_w = int(h * aspect_dst)
            x0 = (w - new_w) // 2
            src_bgr = src_bgr[:, x0:x0 + new_w]
        else:
            # crop height
            new_h = int(w / aspect_dst)
            y0 = (h - new_h) // 2
            src_bgr = src_bgr[y0:y0 + new_h, :]

        return cv2.resize(src_bgr, (target_w, target_h))

    @staticmethod
    def _textbbox(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont) -> Tuple[int, int, int, int]:
        return draw.textbbox((0, 0), text, font=font)

    @staticmethod
    def _expand_bbox(b: Tuple[int, int, int, int], pad: int) -> Tuple[int, int, int, int]:
        x0, y0, x1, y1 = b
        return (x0 - pad, y0 - pad, x1 + pad, y1 + pad)

    def _draw_rounded_rect(self, draw: ImageDraw.ImageDraw, box: Tuple[int, int, int, int], radius: int, fill_rgba: Tuple[int, int, int, int]) -> None:
        x0, y0, x1, y1 = box
        w = max(0, x1 - x0)
        h = max(0, y1 - y0)
        if w == 0 or h == 0:
            return
        r = max(0, min(radius, min(w, h) // 2))
        draw.rounded_rectangle(box, radius=r, fill=fill_rgba)

    def render_datetime_and_weather(
        self,
        frame_bgr: np.ndarray,
        margins: Dict[str, int],
        weather: Dict,
        datetime_corner: str = "bottom-left",
        weather_corner: str = "bottom-right",
        font_color: Tuple[int, int, int] = (255, 255, 255),
        contrast_text: bool = False,
    ) -> np.ndarray:
        # Cache key: recompute the PIL text render only when the clock second
        # or weather changes -- reduces the expensive PIL pass from 30x/sec
        # to 1x/sec. The blend below still runs every frame (the base photo
        # moves under Ken Burns), but only over the cached overlay's bounding
        # box, not the full frame -- avoids a full-frame float32
        # convert/multiply/add on every compositor tick just to paint a
        # small text corner.
        tick = time.strftime("%H:%M:%S")
        cache_key = (tick, repr(weather), repr(margins), datetime_corner, weather_corner, font_color)
        if cache_key != getattr(self, "_overlay_cache_key", None):
            overlay_rgba = self.render_overlay_rgba(
                self.desired_w, self.desired_h, margins, weather,
                datetime_corner=datetime_corner,
                weather_corner=weather_corner,
                font_color=font_color,
            )
            alpha = overlay_rgba.split()[3]
            bbox = alpha.getbbox()
            self._cached_bbox = bbox
            if bbox is not None:
                mask = np.array(alpha.crop(bbox), dtype=np.float32)[..., None] / 255.0
                self._cached_mask = np.repeat(mask, 3, axis=2)
                text_rgb = np.array(overlay_rgba.crop(bbox).convert("RGB"))
                self._cached_text_bgr = cv2.cvtColor(text_rgb, cv2.COLOR_RGB2BGR).astype(np.float32)
            self._overlay_cache_key = cache_key

        bbox = self._cached_bbox
        if bbox is None:
            return frame_bgr
        x0, y0, x1, y1 = bbox
        out = frame_bgr.copy()
        region = out[y0:y1, x0:x1].astype(np.float32)
        blended = self._cached_text_bgr * self._cached_mask + region * (1.0 - self._cached_mask)
        out[y0:y1, x0:x1] = blended.astype(np.uint8)
        return out
        
    # overlay.py  --- add this new method inside OverlayRenderer
    def render_overlay_rgba(
        self,
        width: int,
        height: int,
        margins: Dict[str, int],
        weather: Dict,
        datetime_corner: str = "bottom-left",
        weather_corner: str = "bottom-right",
        font_color: Tuple[int, int, int] = (255, 255, 255),
    ) -> Image.Image:
        # Transparent canvas
        overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)

        # Clock strings
        current_time = time.strftime("%H:%M:%S")
        current_date = time.strftime("%d/%m/%y")

        # Margins
        ml = int(margins.get("left", 50))
        mb = int(margins.get("bottom", 50))
        mr = int(margins.get("right", 50))
        mt = int(margins.get("top", margins.get("bottom", 50)))
        # Support both legacy "spacing" and newer "spacing_between" keys.
        spacing = int(margins.get("spacing_between", margins.get("spacing", 10)))

        # Measure datetime block
        tb = draw.textbbox((0, 0), current_time, font=self.time_font)
        db = draw.textbbox((0, 0), current_date, font=self.date_font)
        t_w, t_h = tb[2] - tb[0], tb[3] - tb[1]
        d_w, d_h = db[2] - db[0], db[3] - db[1]

        # Datetime block anchor from corner
        block_total_w = max(t_w, d_w)
        block_total_h = t_h + spacing + d_h
        if datetime_corner == "bottom-left":
            bx, by = ml, height - mb - block_total_h
        elif datetime_corner == "bottom-right":
            bx, by = width - mr - block_total_w, height - mb - block_total_h
        elif datetime_corner == "top-left":
            bx, by = ml, mt
        elif datetime_corner == "top-right":
            bx, by = width - mr - block_total_w, mt
        else:
            bx, by = ml, height - mb - block_total_h

        x_time = bx + (block_total_w - t_w) // 2
        y_time = by
        x_date = bx + (block_total_w - d_w) // 2
        y_date = by + t_h + spacing

        # Weather block
        right_positions = []
        temp_text = None
        cond_text = None
        if weather:
            temp_text = f"{weather.get('temp', '--')}°{weather.get('unit', '')}"
            cond_text = (weather.get('description') or '').strip() or None

        if temp_text:
            temp_bb = draw.textbbox((0, 0), temp_text, font=self.time_font)
            temp_w = temp_bb[2] - temp_bb[0]
            temp_h = temp_bb[3] - temp_bb[1]

            cond_w = cond_h = 0
            if cond_text:
                cond_bb = draw.textbbox((0, 0), cond_text, font=self.date_font)
                cond_w = cond_bb[2] - cond_bb[0]
                cond_h = cond_bb[3] - cond_bb[1]

            block_w = max(temp_w, cond_w)
            block_h = temp_h + (6 + cond_h if cond_text else 0)

            if weather_corner == "bottom-left":
                wx, wy = ml, height - mb - block_h
            elif weather_corner == "bottom-right":
                wx, wy = width - mr - block_w, height - mb - block_h
            elif weather_corner == "top-left":
                wx, wy = ml, mt
            elif weather_corner == "top-right":
                wx, wy = width - mr - block_w, mt
            else:
                wx, wy = width - mr - block_w, height - mb - block_h

            x_temp = wx + (block_w - temp_w) // 2
            y_temp = wy
            right_positions.append((temp_text, x_temp, y_temp, self.time_font))
            if cond_text:
                x_cond = wx + (block_w - cond_w) // 2
                y_cond = y_temp + temp_h + 6
                right_positions.append((cond_text, x_cond, y_cond, self.date_font))

        rgba_fill = (font_color[0], font_color[1], font_color[2], 255)
        draw.text((x_time, y_time), current_time, font=self.time_font, fill=rgba_fill)
        draw.text((x_date, y_date), current_date, font=self.date_font, fill=rgba_fill)
        for ln, x, y, fnt in right_positions:
            draw.text((x, y), ln, font=fnt, fill=rgba_fill)

        return overlay


    def render_stats(
        self,
        frame_bgr: np.ndarray,
        text: str,
        color_name: str,
        corner: str = "top-left",
        margin_x: int = 20,
        margin_y: int = 20,
    ) -> np.ndarray:
        # Same cache-then-bbox-blend pattern as render_datetime_and_weather:
        # the text only changes when the caller's 5s stats refresh ticks
        # (see PhotoFrameServer._stats_last_refresh), but this used to redo
        # a full-frame PIL fromarray/convert/draw/tobytes round-trip on
        # every single compositor frame regardless.
        h, w = frame_bgr.shape[:2]
        cache_key = (text, color_name, corner, margin_x, margin_y, w, h)
        if cache_key != getattr(self, "_stats_cache_key", None):
            overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
            draw = ImageDraw.Draw(overlay)

            bbox = draw.multiline_textbbox((0, 0), text, font=self.stats_font, spacing=4)
            text_w, text_h = bbox[2] - bbox[0], bbox[3] - bbox[1]

            if corner == "top-left":
                x, y = margin_x, margin_y
            elif corner == "top-right":
                x, y = w - text_w - margin_x, margin_y
            elif corner == "bottom-left":
                x, y = margin_x, h - text_h - margin_y
            elif corner == "bottom-right":
                x, y = w - text_w - margin_x, h - text_h - margin_y
            else:
                x, y = margin_x, margin_y

            color = self._color_from_name(color_name)
            draw.multiline_text((x, y), text, font=self.stats_font, fill=(*color, 255), spacing=4)

            alpha = overlay.split()[3]
            stats_bbox = alpha.getbbox()
            self._stats_bbox = stats_bbox
            if stats_bbox is not None:
                mask = np.array(alpha.crop(stats_bbox), dtype=np.float32)[..., None] / 255.0
                self._stats_mask = np.repeat(mask, 3, axis=2)
                text_rgb = np.array(overlay.crop(stats_bbox).convert("RGB"))
                self._stats_text_bgr = cv2.cvtColor(text_rgb, cv2.COLOR_RGB2BGR).astype(np.float32)
            self._stats_cache_key = cache_key

        stats_bbox = self._stats_bbox
        if stats_bbox is None:
            return frame_bgr
        x0, y0, x1, y1 = stats_bbox
        out = frame_bgr.copy()
        region = out[y0:y1, x0:x1].astype(np.float32)
        blended = self._stats_text_bgr * self._stats_mask + region * (1.0 - self._stats_mask)
        out[y0:y1, x0:x1] = blended.astype(np.uint8)
        return out
