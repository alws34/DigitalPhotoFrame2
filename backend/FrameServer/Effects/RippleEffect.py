import cv2
import numpy as np


def _ease_smoothstep(t: float) -> float:
    # Smooth temporal curve for amplitude/fade.
    return t * t * (3.0 - 2.0 * t)

def _to_3ch_uint8(img):
    """Accept BGR/RGB uint8 3ch, grayscale, or BGRA; return 3ch uint8 contiguous."""
    if img is None:
        raise ValueError("img is None")
    if img.dtype != np.uint8:
        img = img.astype(np.uint8)
    if img.ndim == 2:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    elif img.shape[2] == 4:
        img = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
    elif img.shape[2] != 3:
        img = cv2.cvtColor(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), cv2.COLOR_GRAY2BGR)
    return np.ascontiguousarray(img)

def RippleEffect(
    img1,
    img2,
    duration=0.8,
    fps=30,
    # Multi-ring params
    num_rings=5,          # how many trailing ripples behind the front
    wavelength=120.0,     # pixels between ripples
    ring_width=22.0,      # Gaussian width of each ring (pixels)
    # Amplitude and damping
    max_amplitude=10.0,   # pixels (peak radial displacement)
    damping=1.2,          # temporal damping; higher -> quicker decay
    # Stability/perf
    soft_clip=True,       # clamp sampling maps to valid range
    min_effect_px=0.35    # below this displacement, skip remap and just crossfade
):
    """
    Robust center-out ripple with multiple concentric rings.
      - Sum of `num_rings` Gaussian ring envelopes spaced by `wavelength`.
      - One cv2.remap of img2 per frame, then crossfade with img1.
      - Maps are float32 + contiguous, with clamping and NaN/Inf guards.
      - Falls back to crossfade when amplitude becomes negligible.
    """
    # Sanitize inputs and sizes
    img1 = _to_3ch_uint8(img1)
    img2 = _to_3ch_uint8(img2)
    if img2.shape[:2] != img1.shape[:2]:
        img2 = cv2.resize(img2, (img1.shape[1], img1.shape[0]), interpolation=cv2.INTER_LINEAR)

    rows, cols, _ = img1.shape
    steps = max(1, int(round(float(duration) * float(fps))))

    # Base grids (float32)
    X, Y = np.meshgrid(
        np.arange(cols, dtype=np.float32),
        np.arange(rows, dtype=np.float32),
        indexing="xy"
    )
    cx = np.float32((cols - 1) * 0.5)
    cy = np.float32((rows - 1) * 0.5)

    dx = X - cx
    dy = Y - cy

    # Radius with small epsilon to avoid division by zero
    r = np.sqrt(dx * dx + dy * dy).astype(np.float32)
    r = np.maximum(r, np.float32(1e-6))

    # Unit radial direction; zero exactly at the center for stability
    ux = (dx / r).astype(np.float32)
    uy = (dy / r).astype(np.float32)
    ci = int(round(float(cy)))
    cj = int(round(float(cx)))
    if 0 <= ci < rows and 0 <= cj < cols:
        ux[ci, cj] = 0.0
        uy[ci, cj] = 0.0

    # Max radius to move ring across the frame
    rmax = np.float32(np.sqrt(cx * cx + cy * cy))

    # Constants as float32 to avoid upcasting
    inv_wl = np.float32(2.0 * np.pi / max(1.0, float(wavelength)))
    sig2   = np.float32(2.0 * float(ring_width) * float(ring_width))

    # Work in float32 contiguous buffers
    img2f = np.ascontiguousarray(img2.astype(np.float32))
    img1f = np.ascontiguousarray(img1.astype(np.float32))

    for i in range(steps):
        t  = (i + 1) / steps
        te = _ease_smoothstep(t)

        # Front position
        r0 = np.float32(te) * rmax
        diff = (r - r0).astype(np.float32)

        # Sum of Gaussian rings centered at r0 - k*wavelength, k=0..num_rings-1
        # Small loop (<= ~8) keeps memory/time low while adding visible richness.
        g = np.zeros_like(r, dtype=np.float32)
        wl = np.float32(wavelength)
        for k in range(int(max(1, num_rings))):
            center = r0 - np.float32(k) * wl
            # Gaussian envelope around each ring center
            d = (r - center)
            g += np.exp(-(d * d) / sig2).astype(np.float32)

        # Temporally damped amplitude
        A = np.float32(float(max_amplitude) * np.exp(-float(damping) * float(te)))

        # If amplitude negligible, skip remap (just crossfade)
        if A < np.float32(min_effect_px):
            a = np.float32(te)
            out = (img2f * a + img1f * (1.0 - a)).astype(np.uint8)
            yield out
            continue

        # Sinusoidal displacement modulated by multi-ring envelope
        disp_mag = (A * g * np.sin(diff * inv_wl)).astype(np.float32)

        # Build sampling maps; float32 + contiguous
        map_x = (X + ux * disp_mag).astype(np.float32)
        map_y = (Y + uy * disp_mag).astype(np.float32)

        # Replace non-finite values and optionally clamp to valid coordinates
        np.nan_to_num(map_x, copy=False, nan=0.0, posinf=cols - 1.0, neginf=0.0)
        np.nan_to_num(map_y, copy=False, nan=0.0, posinf=rows - 1.0, neginf=0.0)
        if soft_clip:
            np.clip(map_x, 0.0, cols - 1.0, out=map_x)
            np.clip(map_y, 0.0, rows - 1.0, out=map_y)

        map_x = np.ascontiguousarray(map_x)
        map_y = np.ascontiguousarray(map_y)

        try:
            warped = cv2.remap(
                img2f, map_x, map_y,
                interpolation=cv2.INTER_LINEAR,
                borderMode=cv2.BORDER_REFLECT101
            )
        except cv2.error:
            # Fallback to pure crossfade if remap fails on this frame
            a = np.float32(te)
            out = (img2f * a + img1f * (1.0 - a)).astype(np.uint8)
            yield out
            continue

        # Crossfade toward the warped img2
        a = np.float32(te)
        out = (warped * a + img1f * (1.0 - a)).astype(np.uint8)
        yield out
