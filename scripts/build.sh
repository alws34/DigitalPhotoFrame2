#!/bin/bash
# Build-time test gate for DigitalPhotoFrame.
#
# Runs ruff + pytest inside a throwaway Docker build stage (`backend-test`,
# same base image and pinned deps as the real runtime) before building the
# actual image. If the gate fails, asks whether to build anyway rather than
# silently shipping a broken image or silently blocking the build.
#
# Usage: scripts/build.sh [docker compose args]
#   scripts/build.sh
#   scripts/build.sh -f docker-compose.yml -f docker-compose.pi.yml
set -uo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$APP_DIR"

echo "==> Running test gate (ruff + pytest) in Docker..."
if docker build --target backend-test -t photoframe-test-gate .; then
    echo "==> Test gate passed."
else
    echo ""
    echo "==> TEST GATE FAILED (see output above)."
    reply=""
    if exec 3<>/dev/tty 2>/dev/null; then
        read -r -p "Continue building the image anyway? [y/N] " reply <&3
        exec 3>&-
    else
        echo "==> No interactive terminal available — stopping build."
        exit 1
    fi
    case "$reply" in
        [yY]|[yY][eE][sS])
            echo "==> Continuing despite failing tests."
            ;;
        *)
            echo "==> Build stopped."
            exit 1
            ;;
    esac
fi

echo "==> Building image..."
exec docker compose "$@" build
