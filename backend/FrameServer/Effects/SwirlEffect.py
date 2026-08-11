import cv2
import numpy as np


def _ease(t: float) -> float:
    # Smoothstep
    return t * t * (3.0 - 2.0 * t)

def _to_3ch_uint8(img):
    if img is None:
        raise ValueError("img is None")
    if img.ndim == 2:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    elif img.shape[2] == 4:
        img = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
    if img.dtype != np.uint8:
        img = img.astype(np.uint8)
    return np.ascontiguousarray(img)

def SwirlEffect(
    img1,
    img2,
    duration=0.8,
    fps=30,
    max_angle=np.pi * 0.65,  # radians of twist at center
    falloff=2.2               # higher -> twist decays faster with radius
):
    """
    Center swirl that eases in and fades to img2.
    One remap per frame, robust to grayscale/BGRA inputs.
    """
    img1 = _to_3ch_uint8(img1)
    img2 = _to_3ch_uint8(img2)
    if img2.shape[:2] != img1.shape[:2]:
        img2 = cv2.resize(img2, (img1.shape[1], img1.shape[0]), interpolation=cv2.INTER_LINEAR)

    h, w = img1.shape[:2]
    steps = max(1, int(round(duration * fps)))

    # Base grid
    X, Y = np.meshgrid(
        np.arange(w, dtype=np.float32),
        np.arange(h, dtype=np.float32),
        indexing="xy"
    )
    cx = np.float32((w - 1) * 0.5)
    cy = np.float32((h - 1) * 0.5)

    dx = X - cx
    dy = Y - cy
    r = np.sqrt(dx * dx + dy * dy).astype(np.float32)
    rmax = np.float32(np.sqrt(cx * cx + cy * cy))
    theta = np.arctan2(dy, dx).astype(np.float32)

    img1f = np.ascontiguousarray(img1.astype(np.float32))
    img2f = np.ascontiguousarray(img2.astype(np.float32))

    for i in range(steps):
        t = (i + 1) / steps
        te = _ease(t)

        # Angle offset strongest at center, decays with radius
        k = np.float32(max_angle) * te
        decay = (1.0 - (r / (rmax + 1e-6))) ** np.float32(falloff)
        dtheta = k * np.clip(decay, 0.0, 1.0)

        th = theta + dtheta

        map_x = (cx + r * np.cos(th)).astype(np.float32)
        map_y = (cy + r * np.sin(th)).astype(np.float32)

        # Guard maps
        np.nan_to_num(map_x, copy=False, nan=0.0, posinf=w - 1.0, neginf=0.0)
        np.nan_to_num(map_y, copy=False, nan=0.0, posinf=h - 1.0, neginf=0.0)
        np.clip(map_x, 0.0, w - 1.0, out=map_x)
        np.clip(map_y, 0.0, h - 1.0, out=map_y)

        warped = cv2.remap(
            img2f, np.ascontiguousarray(map_x), np.ascontiguousarray(map_y),
            interpolation=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT101
        )

        a = np.float32(te)
        out = (warped * a + img1f * (1.0 - a)).astype(np.uint8)
        yield out
