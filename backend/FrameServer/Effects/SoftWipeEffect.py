import random as _rand

import numpy as np


def _ease_smoothstep(t: float) -> float:
    # Smooth and monotonic; good for wipes.
    return t * t * (3.0 - 2.0 * t)

def SoftWipeEffect(img1, img2, duration=0.8, fps=30, direction="random", softness=0.08):
    """
    Soft-edge wipe blending img2 over img1.

    direction: 'left','right','up','down','random'
               If 'random', a direction is chosen once and reused for this transition.
    softness : fraction of min(H,W) used as feather width along the wipe edge.
    """
    rows, cols, _ = img1.shape
    steps = max(1, int(round(duration * fps)))
    feather = max(1, int(round(min(rows, cols) * float(softness))))

    # Resolve direction once per transition if requested.
    if direction == "random":
        direction = _rand.choice(["left", "right", "up", "down"])

    # Precompute coordinates
    X = np.arange(cols, dtype=np.float32)[None, :]  # (1, W)
    Y = np.arange(rows, dtype=np.float32)[:, None]  # (H, 1)

    for i in range(steps):
        t = _ease_smoothstep((i + 1) / steps)

        if direction == "left":
            edge = t * cols
            alpha_line = np.clip((edge - X) / max(1.0, feather), 0.0, 1.0)  # (1, W)
            alpha = np.broadcast_to(alpha_line, (rows, cols))
        elif direction == "right":
            edge = (1.0 - t) * cols
            alpha_line = np.clip((X - edge) / max(1.0, feather), 0.0, 1.0)
            alpha = np.broadcast_to(alpha_line, (rows, cols))
        elif direction == "up":
            edge = t * rows
            alpha_col = np.clip((edge - Y) / max(1.0, feather), 0.0, 1.0)   # (H, 1)
            alpha = np.broadcast_to(alpha_col, (rows, cols))
        else:  # down
            edge = (1.0 - t) * rows
            alpha_col = np.clip((Y - edge) / max(1.0, feather), 0.0, 1.0)
            alpha = np.broadcast_to(alpha_col, (rows, cols))

        a = alpha[..., None].astype(np.float32)  # (H, W, 1)
        out = (img2.astype(np.float32) * a + img1.astype(np.float32) * (1.0 - a)).astype(np.uint8)
        yield out
