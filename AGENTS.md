# DigitalPhotoFrame Agent Guide

Shared instructions for Claude Code, Codex, Cline, Cursor, and other coding agents working in this repo.

## Python Runtime

- Use only the project virtual environment at `env/`.
- If `env/` is missing, create it from the repo root with `python -m venv env`.
- Install Python dependencies into `env/`; never install project packages globally.
- Preferred bootstrap command: `env/bin/pip install -e . pytest ruff`
- Prefer `env/bin/python` and `env/bin/pip` in docs, scripts, and examples.
- Do not rely on system `python`, `pip`, `pytest`, or `ruff` for project work.
- Claude Code sessions also get a project hook that prepends the repo venv to `PATH` automatically. Treat that as convenience, not permission to use global tooling.

## Source Of Truth

- `README.md`: product behavior, user-facing terminology, stream semantics.
- `deploy.md`: backend/frontend deployment and build expectations.
- `photoframe_settings.example.json`: settings shape and safe defaults (migration seed only; live settings are in SQLite).
- `Utilities/config_store.py`: settings schema, defaults, and load/save behavior. `Settings.py` does not exist in this repo - if referenced anywhere (including older docs), that's stale.
- `pyproject.toml`: Python version, package metadata, Ruff, and pytest config.
- `frontend/package.json`: frontend scripts and JS dependency versions.

## Architecture

- `app.py`: entry point; selects pygame vs headless mode and wires `PhotoFrameServer`, `Backend`, `MqttBridge`, and `AutoUpdater`.
- `FrameServer/`: compositor, image loading, transitions, overlays, and frame production.
- `FrameGUI/`: pygame fullscreen UI (`photoframe_view_pygame.py`), including its own on-screen settings panel.
- `WebAPI/`: Flask backend, auth, settings/images routes, and serving of `frontend/dist`.
- `frontend/`: Vite/React admin UI for login, stream, gallery, and settings.
- `Utilities/`: weather providers, MQTT, scheduling, autoupdate, brightness, and helpers.
- `Tests/`: pytest regression coverage.

## Guardrails

- Prefer small, surgical changes. Preserve both fullscreen GUI mode and headless mode unless the task explicitly changes them.
- This app runs unattended on Raspberry Pi and desktop systems. Fail soft on weather, network, MQTT, filesystem, and external API issues; log clearly and keep the frame loop and API alive.
- Treat `FrameServer/` and overlay code as hot paths. Avoid blocking I/O, large frame copies, and unnecessary per-frame work.
- Keep settings changes end-to-end. If you change a setting, update the example JSON, persistence path, backend route handling, and every UI/editor that exposes it.
- Flask serves the built frontend from `frontend/dist`. Any React UI change should be followed by a frontend rebuild.
- Keep paths and secrets portable. Do not hardcode machine-specific paths, API keys, or OS-only assumptions when the repo already has a pattern for them.
- Preserve auth checks and handle uploads/filesystem input defensively.

## Verification

- Python/backend work: run the relevant checks from `env/` when feasible.
- Frontend work: run `cd frontend && npm run lint` and `cd frontend && npm run build` when feasible.
- Stream, render, or settings work: do a headless smoke test with `env/bin/python app.py --headless` when feasible.
