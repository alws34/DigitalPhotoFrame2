# Kiosk touch/keyboard handoff

**Status: RESOLVED.** Touch, on-screen keyboard, tab switching, dropdowns,
and scrolling all work. One known limitation remains (scrolling is usable
but not silky-smooth — see "Known limitation" at the end). This doc replaces
the previous in-progress handoff; the sections below cover the full fix
history across two sessions.

## Root causes found and fixed (session 1)

### 1. Keyboard never appeared

**Cause:** `GTK_IM_MODULE` was never set in the container environment, so
GTK3 never talked to the Wayland text-input protocol and squeekboard never
learned a text field gained focus.

**Fix:** `GTK_IM_MODULE=wayland` added to `docker-compose.pi.yml`'s
`environment` block. Still in place.

### 2. Keyboard appeared but was invisible

**Cause:** the kiosk window used `window.fullscreen()`. labwc stacks an
exclusive-fullscreen surface above every layer-shell layer, including
"overlay" — where squeekboard draws.

**Fix:** stopped using `window.fullscreen()`; window is a normal toplevel
positioned via a labwc windowRule instead (see #3).

### 3. Losing fullscreen broke full-screen coverage

**Fix:** `~/.config/labwc/rc.xml` (host-level, **outside the git repo**)
windowRule for `identifier="webview_launcher.py"`:
```xml
<windowRules>
  <windowRule identifier="webview_launcher.py" serverDecoration="no" fixedPosition="yes" />
  <windowRule identifier="webview_launcher.py">
    <action name="MoveTo" x="0" y="0" />
    <action name="ResizeTo" width="1280" height="720" />
  </windowRule>
</windowRules>
```
`fixedPosition="yes"` was required to stop `wf-panel-pi`'s 36px reserved
exclusive zone from shrinking the window. `serverDecoration`/windowRule
properties apply "on first map" only — testing changes here needs a full
container restart, not just a triple-tap reveal. GTK's own geometry getters
(`get_root_origin()`, `get_size()`) are **not reliable on Wayland**; `grim`
screenshots were the only trustworthy verification.

## Root causes found and fixed (session 2 — touch coordinates + polish)

### 4. Touch coordinates — wrong at the host level, not the app level

The real bug was never in application code: the Goodix touch panel is
native `720x1280` (portrait), but labwc applies output `Transform=270` to
present it as `1280x720` landscape (confirmed via `wlr-randr`). Every app
that opened a text field or dropdown was getting raw, unrotated touch
coordinates and every prior session's app-side correction formula was
chasing a symptom, calibrated for whatever window mode was active at the
time and breaking again the next time the window mode changed.

**Fix:** a libinput calibration matrix, applied once at the host/udev level
— matches the Raspberry Pi docs' own guidance ("configure touch rotation in
your input library or desktop") instead of app code:

`/etc/udev/hwdb.d/99-touchscreen-calibration.hwdb`:
```
evdev:name:Goodix Capacitive TouchScreen:*
 LIBINPUT_CALIBRATION_MATRIX=0 1 0 -1 0 1
```
Applied via `sudo systemd-hwdb update && sudo udevadm trigger --subsystem-match=input --action=change`.
Verified against `libinput debug-events` cross-checked with real taps.

Every Wayland client (webview, squeekboard, labwc itself) now gets correct
coordinates for free — no per-app correction needed anywhere.

**All app-side touch coordinate code was removed** from `webview_launcher.py`
per explicit decision to trust the host rather than keep re-deriving
formulas per window mode. `on_any_event`'s custom `Gdk.event_handler_set`
override (previously used for coordinate rewriting + logging + an
OSK-outside-tap-close heuristic) was deleted entirely; GDK/WebKit dispatch
events normally.

### 5. `OskGuard.is_visible()` always returned `False` (pre-existing bug)

**Cause:** `Visible` is a DBus *property* (`org.freedesktop.DBus.Properties`),
not a method — `busctl call sm.puri.OSK0 ... GetVisible` fails with "Method
GetVisible is not implemented", `busctl get-property ... Visible` works.
`GDBusProxy.get_cached_property()` also doesn't help, since squeekboard
doesn't implement `GetAll` (only the single-property `Get`), so the property
cache is never populated.

This bug meant a "force-hide OSK on outside tap" safety net silently never
fired. Once fixed, that safety net turned out to be actively harmful (next
item) and was removed entirely rather than kept.

### 6. Keyboard closed on field N+1 and never reopened

**Cause:** the (now correctly-firing) "force-hide OSK on outside tap"
safety net fired on *every* tap in the upper ~55% of the screen while the
OSK was visible — including a tap on the *next* input field, since most
fields sit up there. It raced squeekboard's own focus-driven show/hide via
the text-input protocol, killing the keyboard instead of letting it reopen.

**Fix:** removed `OskGuard` and the safety-net entirely from
`webview_launcher.py`. squeekboard's automatic focus/blur-driven show/hide
needs no app help.

### 7. `NameError: name 'GLib' is not defined` (self-inflicted, same day)

Removing `OskGuard`'s DBus code also accidentally dropped `GLib` from the
`gi.repository` import line, even though `main()` still uses
`GLib.timeout_add`/`unix_signal_add`/`SOURCE_CONTINUE`/`SOURCE_REMOVE`. This
crashed the SIGUSR1 reveal handler, so triple-tap silently did nothing.
Fixed by restoring `GLib` to the import (only `Gio`, genuinely unused after
`OskGuard`'s removal, was dropped).

### 8. UI polish

- Bigger touch targets globally (`frontend/src/index.css`, `SettingsView.jsx`
  tab buttons): larger padding/min-height on inputs, selects, buttons,
  toggles; `:root` base font size 15px → 18px.
- Missing Network/Profile settings tabs: not a real regression — the local
  Mac clone used to build the frontend was 6+ commits stale vs. the Pi's own
  repo. Fixed by building from the Pi's actual current source.
- Invisible `<select>` dropdown text: WebKitGTK renders `<select>` with
  native GTK chrome regardless of CSS `background`/`color` unless
  `appearance: none` is set. Fixed with an explicit `select { appearance:
  none; -webkit-appearance: none; ... }` rule plus a custom SVG arrow
  (`appearance: none` removes the native one too).

### 9. Scrolling/interaction lag investigation

Reported after the above fixes: laggy scrolling, and a freeze opening a
`<select>` popup. Root-caused in three layers, cheapest fix first:

1. **`backdrop-filter: blur(20px)` on `.glass`/`.glass-panel`** (used by
   nearly every panel, including the settings container itself). WebKitGTK
   here runs Cairo *software* compositing
   (`HardwareAccelerationPolicy.NEVER` — see below for why), so a 20px
   gaussian blur recomputes on the CPU every scroll/repaint frame.
   **Fix:** `--glass-blur` set to `0px` in `index.css`.
2. **The scrollable settings panel carried its own `box-shadow`** — a
   `box-shadow` on the exact element that scrolls defeats WebKit's
   blit-and-shift fast-scroll path, forcing a full repaint every scroll
   tick. **Fix:** split `SettingsView.jsx`'s `.glass` container into a
   non-scrolling outer wrapper (keeps border/shadow/radius) and a plain
   inner `overflowY: auto` div with no decoration.
3. **pygame kept compositing/flipping frames at ~60Hz underneath the
   fullscreen kiosk webview**, even though nothing was visible, starving
   WebKitWebProcess of CPU during touch/scroll. **Fix:** `PhotoFramePygame`
   now tracks `_kiosk_visible` (set `True` when it sends `SIGUSR1` to
   reveal, cleared by a `SIGUSR2` handler); `webview_launcher.py`'s
   `hide_window()` (close button + delete-event, now unified into one
   function) sends `SIGUSR2` back to `os.getppid()` when it hides.
   `app_modes.py`'s main loop skips `view.render_pending_frame()` while
   `view.is_kiosk_visible()` is true.

Together these took scrolling from "chaotic/frozen" to "usable but not
buttery" — see Known limitation below for why it doesn't go further.

### 10. GPU acceleration — tried, reverted, do not retry without new evidence

Hypothesis: `LIBGL_ALWAYS_SOFTWARE=1` is set container-wide in
`docker-compose.yml` ("Mesa software fallback... for pygame's benefit"), so
even `HardwareAccelerationPolicy.ALWAYS` on the WebKit side was still
routing through Mesa llvmpipe (CPU) — never real VC4/V3D GPU. Tried scoping
the env var to exclude just the `webview_launcher.py` child process (which
also owns `WebKitWebProcess`, spawned as its child and inheriting its env).

**Result: broke the display.** `grim` screenshots came back as scanline
garbage — a corrupted framebuffer readout. Most likely labwc's own DRM/KMS
ownership conflicting with a second real GPU context from WebKit. **Fully
reverted** — `HardwareAccelerationPolicy.NEVER` restored,
`webview_launcher.py`'s subprocess spawn goes back to inheriting the full
(software-forced) environment unmodified. Do not re-attempt this without
new evidence the DRM conflict is resolved (e.g. a compositor upgrade, or
confirmation labwc can share DRM master with a second EGL/GBM client).

## Known limitation

Scrolling and popups (e.g. the `<select>` dropdown) are usable but not
smooth — `WebKitWebProcess` pegs a full core during active scroll/touch.
This is Cairo software rasterization cost on a 4-core Pi with no path to
real GPU acceleration available (see #10). The self-inflicted costs (blur,
scroll-container shadow, pygame contention) are gone; what's left is close
to a hardware/software-stack ceiling for WebKitGTK on this device. Further
gains, if ever needed, would mean either simplifying the settings UI's DOM
further (virtualizing long lists, removing remaining per-row transitions)
or replacing WebKitGTK with a differently-accelerated embed (e.g. Chromium
via ozone-wayland, a bigger change not attempted here).

## Files touched (final state, both this doc's location and the running
container in sync)

- `backend/FrameGUI/kiosk/webview_launcher.py` — no app-side touch
  coordinate code; `OskGuard` removed; `hide_window()` unified +
  `SIGUSR2`-to-parent; `HardwareAccelerationPolicy.NEVER`.
- `backend/FrameGUI/photoframe_view_pygame.py` — `_kiosk_visible` tracking,
  `SIGUSR2` handler, `is_kiosk_visible()`; `_spawn_kiosk_process()` back to
  unmodified env (GPU revert).
- `backend/app_modes.py` — main loop skips `render_pending_frame()` while
  kiosk is visible.
- `docker-compose.pi.yml` — `GTK_IM_MODULE=wayland` (session 1).
- `frontend/src/index.css` — `--glass-blur: 0px`; bigger touch targets;
  `select { appearance: none; ... }`.
- `frontend/src/pages/SettingsView.jsx` — bigger tab buttons; scroll
  container split from shadow/border wrapper.
- `/etc/udev/hwdb.d/99-touchscreen-calibration.hwdb` (**host-level, outside
  git**) — the actual touch-rotation fix.
- `~/.config/labwc/rc.xml` (**host-level, outside git**) — windowRule from
  session 1, unchanged this session.

No git commits made — all of the above are live working-tree changes on the
Pi's own repo checkout (`~/Desktop/DigitalPhotoFrame`, which was already
several commits ahead of `origin/main` before this session, plus its own
pre-existing uncommitted changes to `Dockerfile`/`API.py`). Nothing pushed.
