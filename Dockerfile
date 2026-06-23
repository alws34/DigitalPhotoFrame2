# ==============================================================
# Stage 1: Build React frontend
# ==============================================================
FROM node:20-slim AS frontend-build

WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ==============================================================
# Stage 2: Python runtime (pygame display + Flask backend)
# ==============================================================
FROM python:3.11-slim

LABEL maintainer="DigitalPhotoFrame"
LABEL description="Digital Photo Frame - pygame display + Flask backend"

WORKDIR /app

# System dependencies for OpenCV, Pillow, pillow-heif, and SDL2 (pygame)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    libsm6 \
    libxrender1 \
    libxext6 \
    libjpeg62-turbo \
    libopenjp2-7 \
    libheif1 \
    libde265-0 \
    libsdl2-2.0-0 \
    libsdl2-image-2.0-0 \
    libsdl2-mixer-2.0-0 \
    libsdl2-ttf-2.0-0 \
    libwayland-client0 \
    libwayland-egl1 \
    libwayland-cursor0 \
    libxkbcommon0 \
    libegl1 \
    libegl-mesa0 \
    libgles2 \
    libgbm1 \
    curl \
    network-manager \
    wireless-tools \
    iw \
    tzdata \
    libcap2-bin \
    wlr-randr \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements-docker.txt ./
RUN pip install --no-cache-dir -r requirements-docker.txt

# Copy application code
ENV PF_DB_PATH=/data/photoframe.db

COPY app.py app_modes.py config.py pyproject.toml ./
COPY FrameServer/ ./FrameServer/
COPY FrameGUI/ ./FrameGUI/
COPY WebAPI/ ./WebAPI/
COPY Utilities/ ./Utilities/
COPY arial.ttf ./

# Copy built frontend from stage 1
COPY --from=frontend-build /build/frontend/dist ./frontend/dist

# Grant CAP_NET_BIND_SERVICE so UID 1000 can bind port 80 without root
RUN setcap 'cap_net_bind_service=+ep' /usr/local/bin/python3.11

# Create default directories
RUN mkdir -p /app/Images /data && chown -R 1000:1000 /app /data

# Settings and images are expected as volumes
VOLUME ["/app/Images", "/data"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -sf http://localhost:80/ || exit 1

# Default: pygame display mode (use --headless for server-only)
ENTRYPOINT ["python", "app.py"]
CMD ["--display", "pygame"]
