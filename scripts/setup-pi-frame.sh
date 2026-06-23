#!/usr/bin/env bash
# One-time host setup for unattended Raspberry Pi photo frame operation.
# Run as: sudo bash scripts/setup-pi-frame.sh
#
# What this does:
#   1. Prevents the Pi from suspending/sleeping when the screen turns off.
#   2. Disables WiFi power management so the Pi stays reachable at all times.
#   3. Disables the compositor screensaver/DPMS auto-blank (lets the app control it).

set -euo pipefail

echo "=== DigitalPhotoFrame: Pi host setup ==="

# ── 1. Disable systemd sleep/suspend/hibernate targets ──────────────────────
# Without this the Pi may suspend when the display goes off, dropping SSH and
# the web interface entirely.
echo "[1/3] Masking systemd sleep targets..."
systemctl mask --now \
    sleep.target \
    suspend.target \
    hibernate.target \
    hybrid-sleep.target \
    2>/dev/null || true
echo "  Done — system will no longer auto-suspend."

# ── 2. Disable WiFi power management via NetworkManager ─────────────────────
# By default the kernel can power-save the WiFi adapter, causing brief
# disconnects that look like the Pi went offline.
echo "[2/3] Disabling WiFi power management..."
NM_CONF=/etc/NetworkManager/conf.d/wifi-powersave-off.conf
cat > "$NM_CONF" <<'EOF'
[connection]
wifi.powersave = 2
EOF
echo "  Written $NM_CONF"

# Apply immediately if NetworkManager is running
if systemctl is-active --quiet NetworkManager 2>/dev/null; then
    systemctl reload NetworkManager 2>/dev/null || true
fi

# Also set directly on the live interface (best-effort)
WIFI_IFACE=$(iw dev 2>/dev/null | awk '/Interface/{print $2}' | head -1)
if [ -n "$WIFI_IFACE" ]; then
    iwconfig "$WIFI_IFACE" power off 2>/dev/null && \
        echo "  Set $WIFI_IFACE power off (immediate)" || \
        echo "  (iwconfig not available or failed — NM config will apply on next connect)"
fi

# ── 3. Disable Wayland/X11 compositor screensaver and DPMS auto-blank ───────
# The photo frame app controls the display schedule itself.  The OS screensaver
# would blank the screen independently and sometimes trigger DPMS suspend events.
echo "[3/3] Disabling OS screensaver / DPMS auto-blank..."

# labwc / wlroots: wlr-randr has no screensaver knob; disable via wlopm if present
if command -v wlopm &>/dev/null; then
    wlopm --on "$(wlr-randr 2>/dev/null | awk '/enabled/{print $1; exit}')" 2>/dev/null || true
fi

# X11 (fallback or side sessions)
if command -v xset &>/dev/null && [ -n "${DISPLAY:-}" ]; then
    xset s off -dpms 2>/dev/null || true
    echo "  X11: screensaver and DPMS disabled."
fi

# lightdm / Raspberry Pi compositor: drop xscreensaver if installed
if dpkg -l xscreensaver 2>/dev/null | grep -q '^ii'; then
    apt-get remove -y --purge xscreensaver xscreensaver-data 2>/dev/null || true
    echo "  Removed xscreensaver."
fi

# ── 4. Allow UID 1000 to write backlight brightness/power via udev ───────────
# The container runs as UID 1000.  Without this rule, writing to
# /sys/class/backlight/*/bl_power requires root.
echo "[4/4] Installing udev backlight rule for UID 1000..."
UDEV_RULE=/etc/udev/rules.d/90-backlight-photoframe.rules
cat > "$UDEV_RULE" <<'EOF'
# Allow the photoframe user (UID 1000) to control the backlight
SUBSYSTEM=="backlight", ACTION=="add", RUN+="/bin/chgrp video /sys/class/backlight/%k/brightness /sys/class/backlight/%k/bl_power", RUN+="/bin/chmod g+w /sys/class/backlight/%k/brightness /sys/class/backlight/%k/bl_power"
EOF
udevadm control --reload-rules 2>/dev/null || true
udevadm trigger --subsystem-match=backlight 2>/dev/null || true
echo "  Written $UDEV_RULE — UID 1000 can now write backlight controls."

# Add pi user to video group if not already there
if ! groups pi 2>/dev/null | grep -q '\bvideo\b'; then
    usermod -aG video pi 2>/dev/null && echo "  Added pi to video group."
fi

echo ""
echo "=== Setup complete. Reboot recommended for all changes to take effect. ==="
