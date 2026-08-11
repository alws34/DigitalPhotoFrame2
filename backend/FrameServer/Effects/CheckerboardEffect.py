import numpy as np


def _ease_smoothstep(t: float) -> float:
    return t * t * (3.0 - 2.0 * t)

def CheckerboardEffect(img1, img2, duration=0.8, grid_size=16, fps=30):
    rows, cols, _ = img1.shape
    steps = max(1, int(round(duration * fps)))

    cell_w = max(1, cols // max(1, grid_size))
    cell_h = max(1, rows // max(1, grid_size))

    y = np.arange(rows)[:, None]      # (rows,1)
    x = np.arange(cols)[None, :]      # (1,cols)
    ci = np.minimum(y // cell_h, grid_size - 1)  # (rows,1)
    cj = np.minimum(x // cell_w, grid_size - 1)  # (1,cols)
    even_cell = ((ci + cj) % 2) == 0            # (rows,cols) broadcasted

    for i in range(steps):
        t = _ease_smoothstep((i + 1) / steps)
        current_h = int(round(t * cell_h))

        within_col = (y % cell_h) < current_h           # (rows,1)
        within_full = np.broadcast_to(within_col, (rows, cols))  # (rows,cols)

        mask2 = np.where(even_cell, True, within_full)  # (rows,cols)
        yield np.where(mask2[..., None], img2, img1)    # (rows,cols,1) vs (rows,cols,3)
