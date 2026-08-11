import random

import numpy as np


def _ease_smoothstep(t: float) -> float:
    return t * t * (3.0 - 2.0 * t)

def ScrollEffect(img2, img1, duration=0.8, fps=30, direction=None):
    """
    Scroll reveal between img2 -> img1.
    direction in {'left','right','up','down'} or None (random once).
    """
    rows, cols, _ = img1.shape
    steps = max(1, int(round(duration * fps)))
    if direction is None:
        direction = random.choice(['left', 'right', 'up', 'down'])

    for i in range(steps):
        t = _ease_smoothstep((i + 1) / steps)
        frame = np.empty_like(img1)

        if direction in ('left', 'right'):
            offset = int(round(t * cols))
            offset = max(0, min(cols, offset))
            if direction == 'left':
                # Left area from img2 shifted leftwards by offset
                if cols - offset > 0:
                    frame[:, :cols - offset] = img2[:, offset:]
                if offset > 0:
                    frame[:, cols - offset:] = img1[:, :offset]
            else:  # right
                if offset > 0:
                    frame[:, :offset] = img1[:, cols - offset:]
                if cols - offset > 0:
                    frame[:, offset:] = img2[:, :cols - offset]
        else:
            offset = int(round(t * rows))
            offset = max(0, min(rows, offset))
            if direction == 'up':
                if rows - offset > 0:
                    frame[:rows - offset] = img2[offset:]
                if offset > 0:
                    frame[rows - offset:] = img1[:offset]
            else:  # down
                if offset > 0:
                    frame[:offset] = img1[rows - offset:]
                if rows - offset > 0:
                    frame[offset:] = img2[:rows - offset]
        yield frame
