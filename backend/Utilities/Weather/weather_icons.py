# Utilities/Weather/weather_icons.py
import threading
from pathlib import Path
from typing import Optional, Tuple

try:
    _PIL_OK = True
except Exception:
    _PIL_OK = False

# Open-Meteo WMO code -> canonical icon key + human description.
WMO_TO_ICON = {
    0:  ("clear-day", "Clear sky"),
    1:  ("mainly-clear", "Mainly clear"),
    2:  ("partly-cloudy", "Partly cloudy"),
    3:  ("overcast", "Overcast"),
    45: ("fog", "Fog"),
    48: ("fog-rime", "Depositing rime fog"),
    51: ("drizzle-light", "Light drizzle"),
    53: ("drizzle-moderate", "Moderate drizzle"),
    55: ("drizzle-dense", "Dense drizzle"),
    56: ("freezing-drizzle-light", "Light freezing drizzle"),
    57: ("freezing-drizzle-dense", "Dense freezing drizzle"),
    61: ("rain-slight", "Slight rain"),
    63: ("rain-moderate", "Moderate rain"),
    65: ("rain-heavy", "Heavy rain"),
    66: ("freezing-rain-light", "Light freezing rain"),
    67: ("freezing-rain-heavy", "Heavy freezing rain"),
    71: ("snow-slight", "Slight snow"),
    73: ("snow-moderate", "Moderate snow"),
    75: ("snow-heavy", "Heavy snow"),
    77: ("snow-grains", "Snow grains"),
    80: ("rain-showers-slight", "Light rain showers"),
    81: ("rain-showers-moderate", "Moderate rain showers"),
    82: ("rain-showers-violent", "Violent rain showers"),
    85: ("snow-showers-slight", "Light snow showers"),
    86: ("snow-showers-heavy", "Heavy snow showers"),
    95: ("thunderstorm", "Thunderstorm"),
    96: ("thunderstorm-hail-light", "Thunderstorm with light hail"),
    99: ("thunderstorm-hail-heavy", "Thunderstorm with heavy hail"),
}

ICON_TO_EMOJI = {
    "clear-day": "☀",
    "clear-night": "🌙",
    "mainly-clear": "🌤",
    "partly-cloudy": "⛅",
    "overcast": "☁",
    "fog": "🌫",
    "fog-rime": "🌫",
    "drizzle-light": "🌦",
    "drizzle-moderate": "🌦",
    "drizzle-dense": "🌦",
    "freezing-drizzle-light": "🌧",
    "freezing-drizzle-dense": "🌧",
    "rain-slight": "🌧",
    "rain-moderate": "🌧",
    "rain-heavy": "🌧",
    "freezing-rain-light": "🌧",
    "freezing-rain-heavy": "🌧",
    "snow-slight": "🌨",
    "snow-moderate": "🌨",
    "snow-heavy": "🌨",
    "snow-grains": "🌨",
    "rain-showers-slight": "🌦",
    "rain-showers-moderate": "🌦",
    "rain-showers-violent": "🌧",
    "snow-showers-slight": "🌨",
    "snow-showers-heavy": "🌨",
    "thunderstorm": "⛈",
    "thunderstorm-hail-light": "⛈",
    "thunderstorm-hail-heavy": "⛈",
}

class WeatherIconResolver:
    def __init__(self, project_root: Path, default_size: int = 64):
        self.assets_dir = (project_root / "assets" / "weather-icons").resolve()
        self.assets_dir.mkdir(parents=True, exist_ok=True)
        self.default_size = default_size
        self._lock = threading.Lock()
        self._cache = {}  # (icon_key, size) -> local file path (str)

    def resolve(self, wmo_code: int, is_daytime: Optional[bool], size_px: Optional[int] = None) -> Tuple[str, str]:
        size = size_px or self.default_size
        icon_key, desc = WMO_TO_ICON.get(int(wmo_code), ("overcast", "Unknown"))
        if icon_key == "clear-day" and is_daytime is False:
            icon_key = "clear-night"

        with self._lock:
            cache_key = (icon_key, size)
            if cache_key in self._cache:
                return self._cache[cache_key], desc

            png_path = self.assets_dir / f"{icon_key}.png"
            svg_path = self.assets_dir / f"{icon_key}.svg"

            if png_path.is_file():
                self._cache[cache_key] = str(png_path)
                return str(png_path), desc

            if svg_path.is_file():
                # Let the UI display SVG directly if supported.
                self._cache[cache_key] = str(svg_path)
                return str(svg_path), desc

            # Fallback to emoji PNG
            out = self._render_emoji(icon_key, size)
            self._cache[cache_key] = out
            return out, desc

    def _render_emoji(self, icon_key: str, size: int) -> str:
        out = self.assets_dir / f"__fallback_{icon_key}_{size}.png"
        if out.is_file():
            return str(out)

        if not _PIL_OK:
            # 1x1 transparent placeholder
            with open(out, "wb") as f:
                f.write(bytes.fromhex(
                    "89504E470D0A1A0A0000000D4948445200000001000000010806000000"
                    "1F15C4890000000A49444154789C636000000200010005FE02FEA7F605"
                    "0000000049454E44AE426082"
                ))
            return str(out)

        from PIL import Image, ImageDraw, ImageFont

        emoji = ICON_TO_EMOJI.get(icon_key, "?")
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        # Font selection
        try:
            font = ImageFont.truetype("DejaVuSans.ttf", int(size * 0.8))
        except Exception:
            font = ImageFont.load_default()

        # Pillow >= 8: use textbbox; older fallback uses font.getbbox or getlength
        def _measure(draw, text, font):
            if hasattr(draw, "textbbox"):
                left, t, r, b = draw.textbbox((0, 0), text, font=font)
                return r - left, b - t
            if hasattr(font, "getbbox"):
                left, t, r, b = font.getbbox(text)
                return r - left, b - t
            # Last resort: approximate using textlength and font size
            w = int(getattr(draw, "textlength", lambda *a, **k: len(text) * size * 0.5)(text, font=font))
            h = int(size * 0.8)
            return w, h

        w, h = _measure(draw, emoji, font)
        draw.text(((size - w) / 2, (size - h) / 2), emoji, fill=(255, 255, 255, 255), font=font)
        img.save(out, "PNG")
        return str(out)
