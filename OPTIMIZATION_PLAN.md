# DigitalPhotoFrame — Optimization Plan

Agreed 2026-08-11. YAGNI / native-first review. Execute phases sequentially, in order below. Each phase must land (tests green, smoke-tested on device) before the next starts.

Python standard for all touched/new code: PEP 8, 484 (type hints), 20, 257 (docstrings), 622 (match where it helps), 498 (f-strings), 249 (DB-API/sqlite3 usage). PEP 3333 — disregarded, not a goal.

## Phase 0 — Test safety net (land first)
- Extend `Tests/` with pytest + Flask test client: auth, settings, images upload, albums API, stream smoke test, autoupdate.
- Stdlib `unittest.mock` only — no new test framework/deps.
- Wire frontend lint/build + pytest into `.github/workflows/tests.yml`.
- Goal: every later phase (and every future release push) has a real gate.

## Phase 1 — Dead/legacy code removal
- Delete `FrameGUI/photoframe_view_qt.py` + `FrameGUI/SettingsFrom/*` (Qt dialog/viewmodel/widgets, ~1750 lines). Drop `PySide6` dependency. Remove `app.py --display qt` branch.
- Delete `WebAPI/templates/`, `WebAPI/static/`, `render_template` import/usage — React SPA is sole web UI.
- Kill JSON runtime persistence: remove `metadata.json` read/write in `PhotoFrameServer`. SQLite (`app_settings` / `images_metadata`) becomes sole live store. `photoframe_settings.json` stays migration-seed-only. Untrack `database.db`, `metadata.json`, weather cache JSON from git.
- `stream_fps`: full end-to-end review (schema/default in `config_store.py`, Admin UI slider, MQTT/HA discovery entity, example json) before deciding remove vs wire — report findings, then act on all touchpoints in one pass, nothing left half-wired.
- Immich / Google Photos source drivers: **do not remove** — carried into Phase 3 for bugfix + real implementation.
- Audit `FrameGUI/` for any non-Qt pygame-native settings screens (e.g. `widgets/overlay_panel.py`) — these get superseded by the Phase 2 webview approach, not ported/rewritten.

## Phase 2 — Single control-surface architecture
Problem: two UI codebases (pygame + React) both trying to control all settings/features is duplicated logic by construction. Fix: only React edits settings, ever.
- Triple-tap gesture (already exists, "3 taps → settings menu") stops opening a pygame settings screen. Instead it opens a lightweight embedded webview loading the React admin UI, served locally by the same Flask backend already running.
- Webview tech: WebKitGTK-based (`pywebview` w/ GTK/WebKit backend, or `python-gi` + `WebKit2` directly) — system webview, not a bundled Chromium. Feasibility-check `webkit2gtk` availability/perf on this Pi before committing; fall back plan if unavailable TBD.
- Auth: triple-tap → pygame process requests a short-lived, single-use, loopback-only device token from the backend → webview opens `http://localhost:<port>/?token=...` → backend exchanges it for a normal session cookie once. Token endpoint must hard-reject non-loopback requests.
- `Utilities/config_store.py` remains the single backend-side read/write wrapper over SQLite `app_settings` — both the render loop (read-only) and Flask routes (read/write) go through it. No parallel settings-parsing code anywhere, because there's only one editing UI now.
- Net effect: no MVVM port needed for pygame (nothing left there to port) — pygame becomes render + gesture detection only.

## Phase 3 — Album system: fix + implement
- Local, Immich, and Google Photos sources all in scope — fix the broken selector (`AlbumManager.py`, `frontend/src/pages/AlbumsView.jsx` — 892 lines, expect it shrinks once the actual bug is fixed instead of worked around).
- Full control (local + remote) and streaming for whichever album is active.
- Further functionality beyond "selector actually works + all three sources usable" — deferred until scoped.

## Phase 4 — Performance / RAM / CPU
- Profile first (`py-spy` / `cProfile`, real load on-device) — no blind optimization. Numpy stays; target redundant copies/allocations and any cache that never evicts (thumbnails, weather, frame buffers).
- Verify README's "overlay re-renders ≤1/sec" claim is actually enforced in code.
- Touch-friendliness: mostly solved by Phase 2 (React is the control surface, already a reasonable target for touch styling) — remaining work is tuning React hit-targets/perf on-device, not a second UI codebase.

## Phase 5 — Autoupdate + backup
- Keep tag-gated updater — justified for a remote fleet, not speculative. Review its 618 lines for accidental vs deliberate complexity.
- Backup target moves to the SQLite file once Phase 1 kills JSON — plain `shutil.copy2`, stdlib, no custom serialization.
- Ties into Phase 0's CI gate: autoupdate path should be exercised by the test suite before a version ships to remote devices.

## Open decisions carried forward
- Phase 2 webview backend choice needs on-device feasibility check.
- Phase 1 `stream_fps`: remove vs wire — pending review findings.
