import numpy as np


def _ease_smoothstep(t: float) -> float:
    return t * t * (3.0 - 2.0 * t)

def LinearEffect(img1, img2, duration=0.8, num_blinds=16, direction='vertical', fps=30):
    """
    Deterministic linear wipe that matches your current behavior but vectorized.
    direction='vertical': grows from top. 'horizontal': grows from left.
    """
    rows, cols, _ = img1.shape
    steps = max(1, int(round(duration * fps)))

    for i in range(steps):
        t = _ease_smoothstep((i + 1) / steps)
        if direction == 'vertical':
            current = int(round(t * rows))
            mask = np.zeros((rows, 1), dtype=bool)
            mask[:current, 0] = True
            mask = np.repeat(mask, cols, axis=1)
        else:
            current = int(round(t * cols))
            mask = np.zeros((1, cols), dtype=bool)
            mask[0, :current] = True
            mask = np.repeat(mask, rows, axis=0)
        yield np.where(mask[..., None], img2, img1)
