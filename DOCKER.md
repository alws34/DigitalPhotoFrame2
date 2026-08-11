# Docker Deployment Guide

## Architecture

```
+-------------- Raspberry Pi Host ---------------+
|                                                 |
|  +---- Docker Container ----------------------+ |
|  |  Python Backend (Flask + compositor)        | |
|  |  React Frontend (built, served static)      | |
|  |  pygame / SDL2 (fullscreen display)         | |
|  |                                             | |
|  |  Renders frames directly to display         | |
|  |  via DRM/KMS or Wayland                     | |
|  |  Serves admin UI on port 5002               | |
|  |  Serves MJPEG stream at /api/stream         | |
|  +------+--------------------------------------+ |
|         |                                        |
|  /dev/dri -----------> GPU / DRM-KMS             |
|  /dev/input ---------> Touch / mouse             |
|  /sys/class/backlight -> Brightness control      |
|  Wayland socket -----> Wayland display           |
+---------------------------------------------------+
```

The Docker container runs the full stack: Python backend, React admin UI, and a pygame/SDL2 display client. On the Pi, SDL2 renders frames fullscreen directly to the display via DRM/KMS or Wayland — no browser, no kiosk, no encoding overhead.

**Display modes** (controlled by `app.py`):
- `--display pygame` — SDL2 fullscreen; default in Docker, ~10 MB overhead
- `--headless` — backend + API only, no display window

The MJPEG stream at `/api/stream` is still available for remote viewing from a phone, browser, or a second screen.

## Quick Start (Mac / Development)

### Prerequisites

- Docker Desktop installed
- The repo cloned locally

### Build and run

```bash
cd DigitalPhotoFrame
./scripts/build.sh
docker compose up -d
```

`scripts/build.sh` runs the test gate (ruff + pytest, in Docker) before building the image. On failure it asks whether to build anyway.

On Mac, pygame runs with a dummy display inside the container (no window appears). Use the admin UI and stream endpoint to interact with the app.

Open in your browser:
- **Admin UI:** http://localhost:5002 (login required)
- **MJPEG Stream:** http://localhost:5002/api/stream

### Stop

```bash
docker compose down
```

### Development workflow

1. Edit Python backend or React frontend code
2. Rebuild: `./scripts/build.sh && docker compose up -d`
3. The multi-stage build recompiles the frontend and restarts the backend

For faster frontend iteration, run the Vite dev server separately:
```bash
cd frontend && npm install && npm run dev
# Frontend at http://localhost:5173, proxied API calls go to the container
```

## Raspberry Pi Deployment

### Prerequisites

- Raspberry Pi 4 or 5 (2 GB+ RAM recommended)
- Raspberry Pi OS Bookworm (64-bit recommended)
- Display connected (official Pi display, HDMI, or any other)
- Network connection (Wi-Fi or Ethernet)

### One-Command Install

```bash
git clone https://github.com/alws34/DigitalPhotoFrame.git
cd DigitalPhotoFrame
sudo bash install_docker_kiosk.sh
sudo reboot
```

After reboot, the container starts automatically and renders the photo frame directly to the display.

### What the installer does

1. **Installs Docker** and docker compose plugin
2. **Sets up device permissions** (udev rules for `/dev/dri`, `/dev/input`, backlight)
3. **Builds the Docker container** using `docker-compose.pi.yml` overlay for device access
4. **Creates a systemd service** (`photoframe`) that starts the container on boot

No cage or Chromium is installed by default. The container renders directly to the display via SDL2.

### Pi compose command

The Pi overlay adds GPU, input, backlight, and Wayland socket access:

```bash
docker compose -f docker-compose.yml -f docker-compose.pi.yml up -d
```

SDL2 auto-detects the best driver (`wayland` → `kmsdrm` → `x11`). Override if needed:

```bash
# Headless Pi (no desktop, DRM direct):
SDL_VIDEODRIVER=kmsdrm docker compose -f docker-compose.yml -f docker-compose.pi.yml up -d

# Pi OS with Wayland desktop:
SDL_VIDEODRIVER=wayland docker compose -f docker-compose.yml -f docker-compose.pi.yml up -d
```

### Updating

```bash
./update.sh
```

This does: `git pull` + `scripts/build.sh` (test gate, then build) + `docker compose up -d`.

### Managing services

```bash
# View logs
./logs.sh
# or
docker compose logs -f photoframe

# Restart container
./restart.sh
# or
docker compose restart

# Stop
docker compose down

# Check status
docker compose ps
sudo systemctl status photoframe
```

## Configuration

Settings live in `photoframe_settings.json` (mounted into the container as a volume).

Edit via:
1. **Web UI:** Navigate to http://\<pi-ip\>:5002/settings (login required)
2. **Direct edit:** Modify the JSON file, then `docker compose restart`

### Important settings for Docker

| Setting | Value | Notes |
|---------|-------|-------|
| `backend_configs.server_port` | `5002` | Set `PHOTOFRAME_PORT` env var to match if changed |
| `backend_configs.host` | `0.0.0.0` | Listen on all interfaces (required in container) |
| `system.image_dir` | `Images` | Relative path, inside the container |

### Images

Place photos in the `Images/` directory. It is mounted as a Docker volume, so files persist across container rebuilds.

```bash
# Copy photos
cp /path/to/photos/*.jpg Images/

# Or upload via the Gallery page in the web UI
```

Supported formats: JPG, PNG, GIF, BMP, WebP, HEIC/HEIF, MOV, MP4

### Remote viewing

The MJPEG stream at `/api/stream` works from any device on the same network:

```
http://<pi-ip>:5002/api/stream
```

If you want a second screen showing the stream via Chromium kiosk, set that up separately on the host — it is no longer part of the default install.

### MQTT (Home Assistant)

If using MQTT, the container may need host networking to reach your broker:

```yaml
# In docker-compose.pi.yml, uncomment:
network_mode: host
```

Or set `mqtt.host` to your broker's IP address (not `localhost`).

## Files Overview

| File | Purpose |
|------|---------|
| `Dockerfile` | Multi-stage build (Node frontend + Python 3.11-slim + SDL2 runtime) |
| `docker-compose.yml` | Base compose config (works on Mac and Pi) |
| `docker-compose.pi.yml` | Pi overlay: GPU, input, backlight, Wayland socket |
| `requirements-docker.txt` | Pinned Python deps (opencv-headless + pygame) |
| `install_docker_kiosk.sh` | One-command Pi setup (Docker + device permissions + systemd service) |
| `update.sh` | Pull + rebuild + restart |
| `restart.sh` | Restart container |
| `logs.sh` | Tail container logs |

## Resource Usage

Typical on Raspberry Pi 4 (2 GB):
- **Docker container (Python + pygame):** ~150-250 MB RAM
- **No browser overhead** — pygame renders directly, no Chromium running
- **CPU:** 5-15% idle, 30-50% during transitions
- **Disk:** ~500 MB for Docker image

## Troubleshooting

### Container won't start

```bash
docker compose logs photoframe
```

Common issues:
- Missing `photoframe_settings.json` — copy from example: `cp photoframe_settings.example.json photoframe_settings.json`
- Port 5002 in use — change `PHOTOFRAME_PORT` in `.env` or override in `docker-compose.yml`

### No display / black screen on Pi

The container cannot find a display. Check device access:

```bash
# Confirm /dev/dri exists
ls /dev/dri/

# Make sure you're using the Pi overlay
docker compose -f docker-compose.yml -f docker-compose.pi.yml up -d

# Force a specific SDL video driver
SDL_VIDEODRIVER=kmsdrm docker compose -f docker-compose.yml -f docker-compose.pi.yml up -d
```

Also check logs for SDL errors:
```bash
docker compose logs photoframe | grep -i sdl
```

### SDL_VIDEODRIVER issues

If pygame fails to open a display, try each driver in order:

```bash
# Direct DRM (no desktop environment needed):
SDL_VIDEODRIVER=kmsdrm

# Wayland (Pi OS Bookworm desktop):
SDL_VIDEODRIVER=wayland

# X11 (if running under an X session):
SDL_VIDEODRIVER=x11
```

Set the chosen value as an environment variable in `docker-compose.pi.yml`.

### No touch input

Verify `/dev/input` is passed through (handled by `docker-compose.pi.yml`) and check available input devices:

```bash
libinput list-devices
```

### Brightness control not working

The container needs `/sys/class/backlight` mounted. Verify:

```bash
# Check backlight device exists
ls /sys/class/backlight/

# Check permissions (should be group=video, mode=0664)
ls -la /sys/class/backlight/*/brightness

# Ensure Pi user is in video group
groups $USER
```

### Rebuilding from scratch

```bash
docker compose down -v          # Remove container + volumes
./scripts/build.sh && docker compose up -d   # Full rebuild (runs test gate first)
```

## Migrating from bare-metal install

If you previously ran the app using `install_photoframe_service.sh` (systemd + venv):

1. Stop the old service: `sudo systemctl stop PhotoFrame_Desktop_App && sudo systemctl disable PhotoFrame_Desktop_App`
2. Your `Images/` directory and `photoframe_settings.json` are preserved (Docker mounts them)
3. Run the Docker installer: `sudo bash install_docker_kiosk.sh && sudo reboot`
4. The old `env/` venv directory can be deleted after verifying Docker works
