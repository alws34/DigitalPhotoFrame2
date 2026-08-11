# Digital Photo Frame

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

A photo-frame compositor for the RaspberryPi 4 and above, using the official RaspberryPi 7" Touchscreen display 2.<br>
Runs on Raspberry Pi or any Linux/macOS/Windows machine in headless mode.<br>
A long lasting memory of your life's moments.

---

## Features

**Display**

- Smooth 30 FPS compositor with 20+ animated transition effects
- Live clock, date, and weather overlay — independently corner-positioned.
- Translucent frosted-glass background effect with configurable blur and shadow
- Hardware stats overlay: CPU usage %, CPU temp, RAM %, free disk space, screen brightness (optional).
- Screen on/off scheduling by hour
- On screen display (with 3 taps) to display the settings menu.

**Admin UI**

- React + Vite frontend with Frosted Glass Elevated dark theme
- User-selectable accent color (indigo / sky / emerald / rose) and motion intensity
- Collapsible sidebar, persisted per-user
- Gallery — browse, upload, delete, and edit image metadata
- Albums — switch between Local images, Immich libraries, and Google Photos albums
- Live View — real-time stream snapshot with optional overlay toggle
- Settings — organized tabs: System, Frame UI, Albums, MQTT, Weather

**Integrations**

- Weather via [Open-Meteo](https://open-meteo.com/) (free, no API key required)
- [Immich](https://immich.app/) photo library integration with rolling prefetch cache
- Google Photos OAuth album support
- MQTT heartbeat + Home Assistant auto-discovery
- Auto-updater via `git pull` on a schedule

---

## Quick Start (Docker — recommended)

```bash
git clone https://github.com/alws34/DigitalPhotoFrame.git
cd DigitalPhotoFrame
cp photoframe_settings.example.json photoframe_settings.json
./scripts/build.sh
docker compose up -d
```

Open in your browser:

- **Admin UI:** http://localhost:5002
- **MJPEG stream:** http://localhost:5002/api/stream

Add photos to `Images/` or upload via the Gallery page.

> See [DOCKER.md](DOCKER.md) for Raspberry Pi deployment, device permissions, display modes, and troubleshooting.

---

## Raspberry Pi (one command)

```bash
git clone https://github.com/alws34/DigitalPhotoFrame.git
cd DigitalPhotoFrame
sudo bash install.sh
sudo reboot
```

The installer sets up Docker, device permissions (GPU, backlight, touch), and a systemd service that starts the frame on boot. After reboot, the frame renders directly to the connected display via SDL2.

---

## Bare-Metal / Development Setup

**Requirements:** Python 3.9+, Node.js 18+

```bash
git clone https://github.com/alws34/DigitalPhotoFrame.git
cd DigitalPhotoFrame

# Python environment
python3 -m venv env
source env/bin/activate          # Windows: env\Scripts\activate
pip install -e .

# Frontend (only needed to serve the built UI via Flask)
cd frontend && npm install && npm run build && cd ..

# Settings
cp photoframe_settings.example.json photoframe_settings.json

# Run
mkdir -p Images
python app.py                    # fullscreen GUI
python app.py --headless         # API + compositor only, no window
```

Admin UI: http://localhost:5002 — create an account on first launch.

### Frontend development

```bash
cd frontend && npm run dev       # Vite dev server at http://localhost:5173
```

Vite proxies API calls to the running Python backend automatically.

---

## Architecture

```
app.py
├── PhotoFrameServer   FrameServer/ — image loading, transitions, overlay baking, frame_to_stream
├── Backend            WebAPI/      — Flask API, auth, gallery, settings, MJPEG stream
├── MqttBridge         Utilities/MQTT/
├── ScreenScheduler    Utilities/screen_scheduler.py
└── AutoUpdater        Utilities/autoupdate_utils.py

frontend/              React + Vite admin UI (served from frontend/dist by Flask)
Utilities/sources/     Photo source drivers: local, immich, google_photos
Utilities/Weather/     Open-Meteo weather provider
```

**Settings** are stored in SQLite (`/data/photoframe.db` in Docker, `WebAPI/database.db` bare-metal). `photoframe_settings.json` is only used once for migration on first run — edits go through the admin UI or API.

**Stream path:** `PhotoFrameServer._send_frame()` bakes overlay + stats onto a BGR frame → `frame_to_stream`. A separate `_raw_frame_to_stream` serves the stream clean (no date/weather overlay) when the overlay toggle is off; stats still appear if enabled. Network delivery is `WebAPI/API.py`'s `_capture_loop` → `_jpeg_queue` → `mjpeg_stream()` (Flask MJPEG generator), gated by `idle_fps` (new-frame pushes go out immediately; otherwise the last JPEG is re-published at `idle_fps` Hz) and encoded per-frame at `stream_width`/`stream_height`/`image_quality_encoding`.

---

## Transition Effects

AlphaDissolve, BarnDoorClose, BarnDoorOpen, Blinds, Checkerboard, CrossZoom, IrisClose, IrisOpen, Linear, LumaWipe, PixelDissolve, Plain, Ripple, Scroll, Shrink, SoftWipe, SpinZoomFade, Stretch, Swirl, Wipe, ZoomBlur, ZoomIn, ZoomOut

Effects are generators that yield `np.uint8 (H, W, 3)` frames. Add a new one in `FrameServer/Effects/` — it is auto-discovered.

---

## Configuration

All settings are editable in the admin UI under **Settings**. Key areas:

| Tab          | What's here                                                                              |
| ------------ | ---------------------------------------------------------------------------------------- |
| **System**   | Server port/host, image directory, screen schedule, auto-update, sidebar                 |
| **Frame UI** | Overlay corner positions, font sizes, date format, effects (blur, shadow), stats display |
| **Albums**   | Active album (local / Immich / Google Photos), sync schedule                             |
| **MQTT**     | Broker host/port, topic, Home Assistant discovery                                        |
| **Weather**  | Latitude, longitude, units, temperature/precipitation units                              |

Changes take effect immediately (hot-reload via settings event bus). Fields marked ⚠ require a restart.

---

## MQTT / Home Assistant

The frame publishes a heartbeat and responds to control messages. Enable in Settings → MQTT. Home Assistant auto-discovery is supported — the frame registers as a `sensor` entity.

---

## Performance (Raspberry Pi)

- Overlay re-renders at most once per second or on weather change (cached RGBA alpha-blend)
- Stats sampled every 5 seconds (psutil, fail-soft)
- Idle streaming re-publishes last frame at `idle_fps` (default 5) — no flicker, no stall
- To reduce CPU/bandwidth: lower `animation_fps` (local render smoothness), lower `idle_fps`, drop `stream_width`/`stream_height` (default 1920×1080), or lower `image_quality_encoding` (default 80).

---

## Contributing

Pull requests welcome. See [CONTRIBUTING.md](CONTRIBUTING.md). Run checks before submitting:

```bash
env/bin/python -m ruff check .
env/bin/python -m pytest
cd frontend && npm run lint && npm run build
```

---

## License

MIT — see [LICENSE](LICENSE).
