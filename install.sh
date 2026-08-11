#!/bin/bash
# =============================================================================
# Digital Photo Frame — Docker Installer
# Target: Raspberry Pi OS Bookworm (64-bit) with Wayland desktop (labwc)
# Usage:  bash install.sh [--user <username>] [--port <port>]
#
# What this does:
#   1. Installs Docker + compose plugin
#   2. Adds the frame user to the right groups (docker, video, render)
#   3. Sets a udev rule so the video group can control backlight brightness
#   4. Generates a .env file with a random secret key
#   5. Creates the Images/ directory and a starter photoframe_settings.json
#   6. Builds and starts the Docker container (auto-restarts on reboot)
#   7. Creates helper scripts: update.sh, restart.sh, logs.sh
# =============================================================================
set -euo pipefail

# ---------------------------------------------------------------------------
# Defaults — override via env or flags
# ---------------------------------------------------------------------------
FRAME_USER="${FRAME_USER:-pi}"
FRAME_PORT="${FRAME_PORT:-5002}"
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

while [[ $# -gt 0 ]]; do
    case $1 in
        --user) FRAME_USER="$2"; shift 2 ;;
        --port) FRAME_PORT="$2"; shift 2 ;;
        -h|--help)
            echo "Usage: bash install.sh [--user <username>] [--port <port>]"
            echo "  --user   OS user that owns the Wayland session (default: pi)"
            echo "  --port   Admin UI port (default: 5002)"
            exit 0 ;;
        *) echo "Unknown argument: $1"; exit 1 ;;
    esac
done

# ---------------------------------------------------------------------------
# Banner
# ---------------------------------------------------------------------------
echo "============================================================"
echo "  Digital Photo Frame — Docker Installer"
echo "============================================================"
echo ""
echo "  App directory : $APP_DIR"
echo "  Frame user    : $FRAME_USER"
echo "  Port          : $FRAME_PORT"
echo ""

# Confirm we are in the repo root
if [[ ! -f "$APP_DIR/docker-compose.yml" ]]; then
    echo "ERROR: docker-compose.yml not found in $APP_DIR"
    echo "       Run install.sh from the DigitalPhotoFrame repo root."
    exit 1
fi

# ---------------------------------------------------------------------------
# Step 1 — Install Docker
# ---------------------------------------------------------------------------
echo "[1/7] Checking Docker..."
if ! command -v docker &>/dev/null; then
    echo "  Installing Docker via get.docker.com..."
    curl -fsSL https://get.docker.com | sh
    sudo usermod -aG docker "$FRAME_USER"
    echo "  Docker installed. Added $FRAME_USER to the 'docker' group."
else
    echo "  Docker already installed: $(docker --version)"
    # Ensure user is in docker group even if Docker was pre-installed
    if ! id -nG "$FRAME_USER" | grep -qw docker; then
        sudo usermod -aG docker "$FRAME_USER"
        echo "  Added $FRAME_USER to the 'docker' group."
    fi
fi

if ! docker compose version &>/dev/null 2>&1; then
    echo "  Installing docker-compose-plugin..."
    sudo apt-get update -qq
    sudo apt-get install -y docker-compose-plugin
fi

# Docker must start on boot so the container restores after a power cycle.
sudo systemctl enable docker

echo "  Docker OK."
echo ""

# ---------------------------------------------------------------------------
# Step 2 — Display & hardware permissions
# ---------------------------------------------------------------------------
echo "[2/7] Configuring display and hardware permissions..."

# video group: DRM/KMS device access (/dev/dri) + backlight sysfs
# render group: GPU acceleration via Mesa (needed on Pi 4/5)
for GRP in video render input; do
    if getent group "$GRP" &>/dev/null; then
        if ! id -nG "$FRAME_USER" | grep -qw "$GRP"; then
            sudo usermod -aG "$GRP" "$FRAME_USER"
            echo "  Added $FRAME_USER to '$GRP' group."
        else
            echo "  $FRAME_USER already in '$GRP' group."
        fi
    fi
done

# Backlight brightness control — allow the video group to write to sysfs
UDEV_RULE='/etc/udev/rules.d/90-backlight.rules'
if [[ ! -f "$UDEV_RULE" ]]; then
    sudo tee "$UDEV_RULE" >/dev/null <<'UDEV'
SUBSYSTEM=="backlight", GROUP="video", MODE="0664"
UDEV
    sudo udevadm control --reload
    sudo udevadm trigger --subsystem-match=backlight 2>/dev/null || true
    echo "  Backlight udev rule written to $UDEV_RULE."
else
    echo "  Backlight udev rule already exists."
fi

echo ""

# ---------------------------------------------------------------------------
# Step 3 — Generate .env (secrets)
# ---------------------------------------------------------------------------
echo "[3/7] Setting up environment file..."
ENV_FILE="$APP_DIR/.env"

if [[ ! -f "$ENV_FILE" ]]; then
    # Generate a random 32-byte hex secret key
    SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))" 2>/dev/null \
             || cat /dev/urandom | tr -dc 'a-f0-9' | head -c 64)
    cat > "$ENV_FILE" <<ENV
# Digital Photo Frame — environment variables
# Generated by install.sh — do not commit this file.
PHOTOFRAME_SECRET_KEY=${SECRET}
PHOTOFRAME_PORT=${FRAME_PORT}
ENV
    echo "  Created .env with a random secret key."
else
    # Ensure port is set even if .env already existed
    if ! grep -q "PHOTOFRAME_PORT" "$ENV_FILE"; then
        echo "PHOTOFRAME_PORT=${FRAME_PORT}" >> "$ENV_FILE"
    fi
    echo "  .env already exists — leaving it unchanged."
fi

echo ""

# ---------------------------------------------------------------------------
# Step 4 — Create data directories + starter settings
# ---------------------------------------------------------------------------
echo "[4/7] Setting up app directories..."

mkdir -p "$APP_DIR/Images"
echo "  Images/ directory ready."

if [[ ! -f "$APP_DIR/photoframe_settings.json" ]] \
   && [[ -f "$APP_DIR/config/photoframe_settings.example.json" ]]; then
    cp "$APP_DIR/config/photoframe_settings.example.json" "$APP_DIR/photoframe_settings.json"
    echo "  Copied config/photoframe_settings.example.json → photoframe_settings.json."
    echo "  Edit this file (or use the web UI) to set your location, MQTT, etc."
fi

echo ""

# ---------------------------------------------------------------------------
# Step 5 — Build and start the Docker container
# ---------------------------------------------------------------------------
echo "[5/7] Building and starting Docker container..."
echo "  (This may take a few minutes on the first run while the image builds.)"
echo ""
cd "$APP_DIR"

"$APP_DIR/scripts/build.sh" -f docker-compose.yml -f docker-compose.pi.yml
docker compose -f docker-compose.yml -f docker-compose.pi.yml up -d

echo ""
echo "  Container started."
echo ""

# ---------------------------------------------------------------------------
# Step 6 — Helper scripts
# ---------------------------------------------------------------------------
echo "[6/7] Writing helper scripts..."

cat > "$APP_DIR/update.sh" <<'SCRIPT'
#!/bin/bash
# Pull the latest code and rebuild the Docker container.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
echo "Pulling latest changes..."
git pull --ff-only
echo "Rebuilding container..."
./scripts/build.sh -f docker-compose.yml -f docker-compose.pi.yml
docker compose -f docker-compose.yml -f docker-compose.pi.yml up -d
echo "Done. Container is running the latest version."
SCRIPT

cat > "$APP_DIR/restart.sh" <<'SCRIPT'
#!/bin/bash
# Restart the running container without rebuilding.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
docker compose -f docker-compose.yml -f docker-compose.pi.yml restart
echo "Container restarted."
SCRIPT

cat > "$APP_DIR/logs.sh" <<'SCRIPT'
#!/bin/bash
# Tail live container logs (Ctrl-C to stop).
cd "$(dirname "${BASH_SOURCE[0]}")"
docker compose -f docker-compose.yml -f docker-compose.pi.yml logs -f --tail=100
SCRIPT

chmod +x "$APP_DIR/update.sh" "$APP_DIR/restart.sh" "$APP_DIR/logs.sh"
echo "  update.sh / restart.sh / logs.sh written."
echo ""

# ---------------------------------------------------------------------------
# Step 7 — Verify
# ---------------------------------------------------------------------------
echo "[7/7] Verifying installation..."
sleep 3
docker compose -f docker-compose.yml -f docker-compose.pi.yml ps
echo ""

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------
LOCAL_IP=$(hostname -I 2>/dev/null | awk '{print $1}' || echo "localhost")
PORT=$(grep -oP '(?<=PHOTOFRAME_PORT=)\d+' "$ENV_FILE" 2>/dev/null || echo "$FRAME_PORT")

echo "============================================================"
echo "  Installation complete!"
echo "============================================================"
echo ""
echo "  Admin UI (any device on your network):"
echo "    http://${LOCAL_IP}:${PORT}"
echo ""
echo "  Remote frame view (stream in browser):"
echo "    http://${LOCAL_IP}:${PORT}/frame"
echo ""
echo "  Useful commands:"
echo "    ./update.sh     — Pull updates & rebuild"
echo "    ./restart.sh    — Restart the container"
echo "    ./logs.sh       — View live container logs"
echo ""
echo "  IMPORTANT: A reboot is required for group membership changes"
echo "  (docker, video, render) to take effect."
echo ""
echo "    sudo reboot"
echo ""
