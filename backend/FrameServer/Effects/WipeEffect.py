import random

import numpy as np


def _ease_smoothstep(t: float) -> float:
    return t * t * (3.0 - 2.0 * t)

def WipeEffect(img1, img2, duration=0.8, fps=30, direction=None):
    """
    Hard wipe from img1 to img2.
    direction in {'left','right','up','down'} or None (random once).
    """
    rows, cols, _ = img1.shape
    steps = max(1, int(round(duration * fps)))
    if direction is None:
        direction = random.choice(['left', 'right', 'up', 'down'])

    for i in range(steps):
        t = _ease_smoothstep((i + 1) / steps)

        if direction in ('left', 'right'):
            w = max(0, min(cols, int(round(t * cols))))
            if direction == 'left':
                mask = np.zeros((rows, cols), dtype=bool)
                if w > 0:
                    mask[:, :w] = True
            else:
                mask = np.zeros((rows, cols), dtype=bool)
                if w > 0:
                    mask[:, cols - w:] = True
        else:
            h = max(0, min(rows, int(round(t * rows))))
            if direction == 'up':
                mask = np.zeros((rows, cols), dtype=bool)
                if h > 0:
                    mask[:h, :] = True
            else:
                mask = np.zeros((rows, cols), dtype=bool)
                if h > 0:
                    mask[rows - h:, :] = True

        yield np.where(mask[..., None], img2, img1)
