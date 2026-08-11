import random

import cv2


def _ease_smoothstep(t: float) -> float:
    return t * t * (3.0 - 2.0 * t)

def StretchEffect(img2, img1, duration=0.8, fps=30, direction=None):
    """
    Grow img1 from an edge over img2.
    direction in {'top','bottom','left','right'} or None (random once).
    """
    rows, cols, _ = img1.shape
    steps = max(1, int(round(duration * fps)))
    if direction is None:
        direction = random.choice(['top', 'bottom', 'left', 'right'])

    for i in range(steps):
        t = _ease_smoothstep((i + 1) / steps)
        frame = img2.copy()

        if direction in ('left', 'right'):
            cur_w = max(1, int(round(cols * t)))
            img1_resized = cv2.resize(img1, (cur_w, rows), interpolation=cv2.INTER_LINEAR)
            if direction == 'left':
                frame[:, :cur_w] = img1_resized
            else:
                frame[:, cols - cur_w:] = img1_resized
        else:
            cur_h = max(1, int(round(rows * t)))
            img1_resized = cv2.resize(img1, (cols, cur_h), interpolation=cv2.INTER_LINEAR)
            if direction == 'top':
                frame[:cur_h] = img1_resized
            else:
                frame[rows - cur_h:] = img1_resized
        yield frame
