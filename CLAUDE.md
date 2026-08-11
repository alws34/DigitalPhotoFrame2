# DigitalPhotoFrame

DigitalPhotoFrame is a long-running Python photo-frame/compositor with an optional PySide6 fullscreen GUI and a Flask-served React admin UI. It targets Raspberry Pi and desktop Linux/Windows, so stability, low overhead, and graceful degradation matter as much as features.

## Python Runtime

- Use only the project virtual environment at `env/`.
- If `env/` is missing, create it from the repo root with `python -m venv env`.
- Install Python dependencies into `env/`; never install project packages globally.
- Preferred bootstrap command: `env/bin/pip install -e . pytest ruff`
- Prefer `env/bin/python` and `env/bin/pip` in commands, scripts, and examples.
- Do not rely on system `python`, `pip`, `pytest`, or `ruff` for project work.
- Claude Code project settings prepend the repo venv to `PATH` at session start, including Claude worktree sessions. Treat that as the default runtime for Python commands.

## Source Of Truth

- `README.md`: product behavior, stream semantics, and user-facing terminology.
- `deploy.md`: backend/frontend build and deployment flow.
- `photoframe_settings.example.json`: settings shape and safe defaults (migration seed only - see below for the live store).
- `Utilities/config_store.py`: settings schema, defaults, and load/save behavior. This is the actual settings module - it's imported by `app.py`, `WebAPI/API.py`, `WebAPI/routes/settings.py`, `FrameServer/PhotoFrameServer.py`, `Utilities/MQTT/mqtt_bridge.py`, `Utilities/AlbumManager.py`, `Utilities/config_events.py`, and the `FrameGUI` views/dialog. **`Settings.py` does not exist in this repo** - if you see it referenced anywhere (including older docs), that's stale; `config_store.py` replaced it.
- Live settings values at runtime are **not** in `photoframe_settings.json` after first boot - they're in SQLite: `/data/photoframe.db` in Docker (`photoframe-data` volume), `WebAPI/database.db` bare-metal; table `app_settings`, single row `key='main'`, `value` = the full settings JSON blob. Read/write it through `config_store.py`, not by hand.
- `pyproject.toml`: Python version, packaging, Ruff, and pytest config.
- `frontend/package.json`: frontend scripts and JS dependency versions.
- `PROJECT_HANDOFF.md` § 7-8: current known issues and the active handoff for in-progress work. Check § 8 specifically before touching the MJPEG stream path (`WebAPI/API.py` `_capture_loop`/`_jpeg_queue`/`mjpeg_stream`) or the settings schema in `config_store.py` - there's an open, already-diagnosed bug there (`stream_fps` is defined and exposed via the Admin UI and MQTT/HA discovery but never actually consumed anywhere in the runtime code) with a ranked fix list already written up.

## Architecture Map

- `app.py`: entry point; selects GUI vs headless mode and wires together `PhotoFrameServer`, `Backend`, `MqttBridge`, and `AutoUpdater`.
- `FrameServer/`: performance-sensitive render/compositor pipeline, image loading, transitions, overlays, and stream frame production.
- `FrameGUI/`: PySide6 fullscreen client plus settings/editor widgets.
- `WebAPI/`: Flask backend, auth, image/settings routes, and static serving of `frontend/dist`.
- `frontend/`: Vite/React admin UI for login, stream, gallery, and settings pages.
- `Utilities/`: weather providers, MQTT, scheduling, autoupdate, brightness, and platform helpers.
- `Tests/`: pytest regression coverage.

## Commands

- `env/bin/python app.py`
- `env/bin/python app.py --headless`
- `env/bin/python -m pytest`
- `env/bin/python -m ruff check .`
- `env/bin/python -m ruff format .`
- `cd frontend && npm run lint`
- `cd frontend && npm run build`

## Project Guardrails

- Prefer small, surgical changes. Preserve both fullscreen GUI mode and headless streaming mode unless the task explicitly changes them.
- This app runs unattended. Fail soft on weather, network, MQTT, filesystem, and external API issues when possible: log clearly and keep the frame loop and API alive.
- Treat `FrameServer/` and overlay code as hot paths. Avoid blocking I/O, unnecessary frame copies, and non-vectorized work in per-frame or per-transition code.
- Keep settings changes end-to-end. If you add, rename, or remove a setting, update the example JSON, `SettingsHandler` flow, backend read/write paths, and every UI/editor that exposes it.
- Flask serves the built frontend from `frontend/dist`. After React UI changes, rebuild the frontend and keep route/API expectations aligned with Flask.
- Keep paths and configuration portable. Do not hardcode local machine paths, secrets, or OS-specific assumptions when repo patterns already handle them.
- Prefer deterministic tools for style and validation. Use Ruff, ESLint, and build/test commands instead of encoding long formatting rules here.
- Be careful with auth, uploads, and filesystem operations. Preserve existing auth checks and handle untrusted files defensively.

## Verification Expectations

- Python/backend changes: run the relevant checks from `env/` when feasible.
- Frontend changes: run `cd frontend && npm run lint` and `cd frontend && npm run build` when feasible.
- Rendering, stream, or settings changes: smoke test with `env/bin/python app.py --headless` when feasible, especially after touching stream delivery, settings reload, or frame generation.

## Claude Code Notes

- `AGENTS.md` exists as the shared cross-agent version of these instructions for tools that look for that filename.
- Additional topic-specific guidance lives under `.claude/rules/`.
- Auto memory is machine-local, not a repo file. The seed notes for this project live in `docs/claude/MEMORY.template.md`; use `/memory` to copy the important bits into Claude's real project memory.
- `.worktreeinclude` copies `photoframe_settings.json` into Claude-created worktrees.
- If Claude Code resume caching or cache-read ratios look wrong, see `docs/claude/cache-fix.md`.
