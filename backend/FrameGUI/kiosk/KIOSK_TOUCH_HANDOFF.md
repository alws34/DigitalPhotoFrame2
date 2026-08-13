# Kiosk touch/keyboard handoff

Status as of this session: on-screen keyboard works, full-screen coverage works,
**touch coordinate mapping into the kiosk window is still wrong** and needs a
fresh diagnostic pass, ideally with a different approach than manual
trial-and-error (see "Recommended next steps").

## What this session was trying to do

Sequence, in order:

1. Fix the on-screen keyboard (squeekboard) never appearing when tapping a
   text field in the kiosk webview.
2. Once the keyboard appeared, fix the fact that tapping it (or anything else)
   didn't land where expected.

Both are entangled with a third, pre-existing constraint: touch coordinates
into this same window were already known to need a correction, calibrated in
an *earlier* session under a different window configuration (`fullscreen()`).
Changing the window configuration to fix the keyboard silently invalidated
that old calibration, and it is not yet clear what (if anything) should
replace it.

## Root causes found and fixed this session

### 1. Keyboard never appeared — fixed

**Cause:** `GTK_IM_MODULE` was never set in the container environment. Without
it, GTK3 uses its default "simple" input-method context, which never talks to
the Wayland text-input protocol, so squeekboard never learns a text field
gained focus.

**Fix:** added `GTK_IM_MODULE=wayland` to `docker-compose.pi.yml`. Confirmed
via `/proc/<pid>/environ` on the `WebKitWebProcess` subprocess that the
variable actually reaches WebKit's renderer process (no sandboxing/bwrap is
in play, so env vars propagate normally).

### 2. Keyboard appeared but was invisible — fixed

**Cause:** the kiosk window used `window.fullscreen()`. labwc stacks an
exclusive-fullscreen surface **above every layer-shell layer, including
"overlay"** — which is where squeekboard draws the keyboard. squeekboard's
`Visible` DBus property correctly flipped to `true` (confirmed via
`busctl --user call sm.puri.OSK0 ... GetVisible`), but nothing rendered,
because the fullscreen kiosk window was compositited on top of it.

**Fix:** stopped using `window.fullscreen()`.

### 3. Losing fullscreen broke full-screen coverage — fixed, but took 3 tries

Once `fullscreen()` was gone, getting the window to actually cover the full
1280x720 screen at (0,0) with no titlebar took several attempts:

- `window.maximize()` alone: window came back sized `1280x684` — 36px short.
  Screenshot confirmed a real SSD titlebar was being drawn (labwc's default
  `<core><decoration>server</decoration>` applies unless overridden).
- Added a labwc window rule
  (`~/.config/labwc/rc.xml`, host-level, **outside the git repo**):
  ```xml
  <windowRules>
    <windowRule identifier="webview_launcher.py" serverDecoration="no" fixedPosition="yes" />
    <windowRule identifier="webview_launcher.py">
      <action name="MoveTo" x="0" y="0" />
      <action name="ResizeTo" width="1280" height="720" />
    </windowRule>
  </windowRules>
  ```
  This required two lessons from `man labwc-config`:
  - `serverDecoration` (and other windowRule properties) apply **"on first
    map"** — i.e. only to a window's very first ever map, not every
    hide/show cycle. Because this process is pre-warmed and kept alive
    (hidden/shown via SIGUSR1, never destroyed), testing a new rc.xml rule
    requires a full **container restart**, not just re-triggering the
    triple-tap reveal.
  - GTK's own geometry getters (`get_root_origin()`, `get_size()`) are **not
    reliable ground truth on Wayland** — there is no protocol for a client to
    query its true on-screen position, so GTK was largely just echoing back
    whatever we last told it via `resize()`/`maximize()`, not compositor
    truth. Screenshots (via `grim`, see below) are the only trustworthy
    signal for actual on-screen geometry.
  - The real remaining 36px gap turned out to be `wf-panel-pi` (the standard
    LXDE-Pi taskbar), a real layer-shell panel reserving 36px at the top of
    the output via an exclusive zone. Every *normal* (non-fullscreen)
    toplevel respects that reservation regardless of requested
    position/size — `pygame`'s fullscreen SDL surface had always silently
    ignored it, so this had never been visible before. `fixedPosition="yes"`
    is the documented labwc property for "ignore reserved output space
    changes caused by... exclusive layer-shell clients such as panels" —
    adding it fixed the last 36px gap.
  - Final confirmed-good geometry (pixel-measured from a `grim` screenshot,
    not GTK APIs): window covers exactly `(0,0)` to `(1280,720)`, no
    titlebar, keyboard still renders on top correctly (this is a normal
    window, not fullscreen, so the layer-shell stacking bug from #2 doesn't
    recur).

## What's still broken: touch coordinates

The window's on-screen geometry is now correct (pixel-verified), but taps
into it are not landing where the user taps.

### The old correction was for a different window mode, and is gone

An earlier session (before this one) had empirically fit a 90°-rotation
affine correction for raw touch coordinates:
```python
_TOUCH_CORRECTION_X = (-0.00092, 1.77660, -3.246)   # a*x + b*y + c
_TOUCH_CORRECTION_Y = (-0.56067, -0.00037, 721.189) # a*x + b*y + c
```
That calibration was done while the window used `fullscreen()`. The working
theory is that wlroots may deliver raw touch coordinates to an
exclusive-fullscreen surface in **untransformed native-panel space**
(720x1280, pre-rotation) as a fast/direct-scanout path, whereas a normal
composited surface (what we have now) gets properly pre-transformed logical
coordinates (1280x720) — meaning the old rotation is not just stale but
actively wrong for the current window mode. This is a **hypothesis**, not
confirmed against labwc/wlroots source.

Evidence for "the old correction is now wrong": a real tap logged
`raw=(1006.2, 452.2)`, which is already a plausible in-window coordinate
(sitting in the on-screen keyboard's top row). Applying the old rotation
mapped it to `(799.3, 156.9)` — which is where the "Image Directory" text
field sits — and that's what actually happened: the field got focused
instead of whatever the user was aiming for, and all keyboard input
afterward went into the wrong field ("jjjjjjjjjjjImages/local_images/...").

**Action taken:** replaced the correction with identity passthrough
(`return x, y`) and redeployed, on the theory that the new window mode needs
no correction at all.

**Result: still not correct.** User reports touch is "still not good" after
this change, even after a full container restart. So identity is not (fully)
correct either.

### Data collected but not yet conclusive

With identity active, real logged taps (raw == corrected, since identity):
```
(1063.1, 568.1)  (1052.4, 553.5)  (956.4, 516.4)  (771.6, 475.3)
(657.8, 443.8)   (926.2, 518.1)   (999.1, 554.6)  (812.4, 532.7)
(499.6, 503.4)   (1112.9, 63.0)   (1070.2, 82.7)  (881.8, 96.2)
(862.2, 90.0)    (945.8, 88.3)    (977.8, 75.9)   (567.1, 397.7)
(949.3, 415.7)   (768.0, 451.1)
```
The cluster around y=63–96 (six points, x=862–1113) looks like repeated
attempts at the settings tab bar (tabs sit at roughly y=84–108 on-screen).
The "Network" tab specifically is at roughly x=774–836 on screen; this
cluster centers noticeably to the right of that (x mostly 862–1113,
closer to "Weather"/"Profile"). That could mean:
- a real, smaller residual offset still exists (not a full 90° rotation, but
  some smaller skew/shift), or
- the user was trying several different tabs across that testing round, not
  repeatedly missing one target — **this was never disambiguated with the
  user**, since a "full handoff" was requested at this point instead.

**This data was never cross-checked against a `grim` screenshot taken at the
same moment**, which is the verification method that worked reliably earlier
in this session (see the coverage-fix section above) and should be applied
here too.

### A second, distinct suspected bug: keyboard-area taps leaking to our window

Multiple logged taps land at y≈396–568, which is *inside* the on-screen
keyboard's visual area (keyboard occupies roughly y=396–706 on a 1280x720
screen, per earlier confirmed screenshots). Taps on the keyboard should be
consumed by squeekboard's own layer-shell surface and never reach our
window's event handler at all (this is what happens for the *majority* of
keypresses — only 3 taps were ever logged by our window in one entire test
session despite ~11 letters being typed, confirming most keyboard taps
correctly bypass us). But *some* keyboard-area taps are clearly reaching our
window instead (logged above), which is a separate potential bug from pure
coordinate correction — possibly a touch-routing/stacking issue at the
compositor level, not something a coordinate formula can fix.

This needs to be investigated separately from the coordinate-mapping
question. It's possible fixing the coordinate mapping incidentally reduces
how often this happens (if it's related to us processing a touch that was
genuinely meant for our window near the keyboard's top edge), but it's also
possible it's an independent stacking/input-region bug.

## Current deployed state (as of end of session)

- Container `photoframe` running, restarted fresh, kiosk process pre-warmed
  (pid 34 at last check).
- `webview_launcher.py`: identity touch-coordinate passthrough, full
  labwc-windowRule-driven geometry (no `fullscreen()`/`maximize()` calls),
  `GLib.timeout_add`-based one-shot geometry logger still present on reveal
  (logs `window_pos`/`window_size`/`alloc`/`monitor0` — useful but proven
  **not fully trustworthy** on Wayland, see above).
- `docker-compose.pi.yml`: `GTK_IM_MODULE=wayland` added to the `environment`
  block.
- `~/.config/labwc/rc.xml` (**host-level, outside git**): `<windowRules>`
  block added for `identifier="webview_launcher.py"` with
  `serverDecoration="no"`, `fixedPosition="yes"`, and `MoveTo`/`ResizeTo`
  actions to `(0,0)`/`1280x720`. Backups of every edit exist alongside it:
  `rc.xml.bak-mouseemu`, `rc.xml.bak-windowrule`, `rc.xml.bak-geomrule`,
  `rc.xml.bak-fixedpos` (plus a pre-existing `rc.bak` from before this
  project).
- No git commits made this session. Nothing pushed.

## Recommended next steps

1. **Stop guessing formulas from scattered real-world taps.** Every prior
   successful calibration in this whole project used *deliberate, single,
   confirmed taps* at known targets, cross-checked against a `grim`
   screenshot taken immediately after. Do that again here: ask for one tap
   at a time on a clearly identifiable, easy-to-verify target (e.g. a
   specific settings tab), read the logged raw coordinate, and compare
   against the known on-screen position of that target (measure from a
   screenshot, don't eyeball it — see the `PIL`-based pixel measurement
   approach used earlier in this session for the coverage-gap diagnosis).
   3–4 points spread across the screen (corners or tab bar + a lower field)
   should be enough to tell identity vs. a small residual offset vs.
   something else entirely.
2. **Separately verify the keyboard-area-leak bug** before assuming a
   coordinate fix will resolve it. Check whether it happens on taps that
   *start* right at the keyboard's top edge (touch-down ambiguity between
   surfaces) vs. taps solidly inside the keyboard. If it's edge-only, it may
   be an inherent, acceptable margin of error. If it happens well inside the
   keyboard's area, that points at a genuine input-routing/stacking bug
   independent of coordinate correction.
3. Consider whether `gtk-layer-shell` (a library that lets a GTK window
   attach as a real wlr-layer-shell surface instead of a normal xdg_toplevel)
   would sidestep this whole category of problem — layer-shell surfaces
   support exact, protocol-level anchoring to all four edges with zero
   margin, without any of `fullscreen()`'s stacking side effects or
   `maximize()`'s placement-heuristic bugs. This would be a more invasive
   change (new system package, code rewrite of the window-creation path) and
   wasn't attempted this session due to time, but may be the more robust
   long-term fix if the labwc-windowRule approach keeps fighting edge cases.
4. Tasks #22 (verify OSK + tap-outside-close) and #23 (delete legacy native
   settings panel) both remain blocked on this being fully resolved.
