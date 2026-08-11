import numpy as np


def _ease_smoothstep(t: float) -> float:
    return t * t * (3.0 - 2.0 * t)

def BlindsEffect(img1, img2, duration=0.8, num_strips=32, fps=30):
    rows, cols, _ = img1.shape
    steps = max(1, int(round(duration * fps)))
    strip_h = max(1, rows // max(1, num_strips))

    y = np.arange(rows)                      # (rows,)
    strip_idx = np.minimum(y // strip_h, num_strips - 1)
    is_even_strip = (strip_idx % 2) == 0

    for i in range(steps):
        t = _ease_smoothstep((i + 1) / steps)
        current_h = int(round(t * strip_h))

        # For odd strips only, rows within the strip become visible as t grows.
        within_strip_y = (y % strip_h) < current_h       # (rows,)
        row_mask = np.where(is_even_strip, True, within_strip_y)   # (rows,)

        mask2 = np.broadcast_to(row_mask[:, None], (rows, cols))   # (rows,cols)
        yield np.where(mask2[..., None], img2, img1)                # (rows,cols,1)
