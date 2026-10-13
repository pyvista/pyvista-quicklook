"""Render a mesh file to a PNG on a transparent background, as pyvista-zstd draws its thumbnails.

Run in the configured PyVista environment: ``python _still.py <source> <png> <width> <height>``.
"""

from __future__ import annotations

import copy
import sys

import numpy as np
import pyvista as pv

# Rendered this many times larger, then averaged down, so edges are smooth.
SCALE = 2
# Space left around the dataset, as a share of the picture's side.
MARGIN = 0.05
MULTI_SAMPLES = 8


def srgb_to_linear(c: np.ndarray) -> np.ndarray:
    """Decode sRGB values in [0, 1] to linear light."""
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def linear_to_srgb(c: np.ndarray) -> np.ndarray:
    """Encode linear-light values in [0, 1] as sRGB."""
    return np.where(c <= 0.0031308, c * 12.92, 1.055 * c ** (1 / 2.4) - 0.055)


def render(source: str, width: int, height: int) -> np.ndarray:
    """Return the dataset drawn on a transparent background, cropped square, as RGBA bytes."""
    dataset = pv.read(source)
    theme = copy.deepcopy(pv.global_theme)
    theme.anti_aliasing = 'msaa'
    theme.multi_samples = MULTI_SAMPLES
    plotter = pv.Plotter(off_screen=True, window_size=[SCALE * width, SCALE * height], theme=theme)
    # Over black, multisampled edges hold colour premultiplied by coverage in linear light.
    plotter.background_color = 'black'
    try:
        plotter.add_mesh(dataset, show_scalar_bar=False)
        image = plotter.screenshot(transparent_background=True, return_img=True)
    finally:
        plotter.close()

    rows, columns = np.nonzero(image[..., 3])
    if rows.size == 0:
        sys.exit(f'{source} draws nothing')
    content = image[rows.min() : rows.max() + 1, columns.min() : columns.max() + 1] / 255
    content[..., :3] = srgb_to_linear(content[..., :3])
    content_height, content_width = content.shape[:2]
    side = max(content_height, content_width)
    side += 2 * int(np.ceil(MARGIN * side))
    side += -side % SCALE
    square = np.zeros((side, side, 4))
    top, left = (side - content_height) // 2, (side - content_width) // 2
    square[top : top + content_height, left : left + content_width] = content

    # Average premultiplied linear channels, then divide out coverage once at the final size.
    small = square.reshape(side // SCALE, SCALE, side // SCALE, SCALE, 4).mean(axis=(1, 3))
    alpha = small[..., 3:]
    straight = np.divide(small[..., :3], alpha, out=np.zeros_like(small[..., :3]), where=alpha > 0)
    rgba = np.concatenate([linear_to_srgb(straight.clip(0, 1)), alpha], axis=-1)
    return np.round(rgba * 255).astype(np.uint8)


def save(rgba: np.ndarray, out: str) -> None:
    """Write RGBA bytes, top row first, as a PNG."""
    height, width = rgba.shape[:2]
    picture = pv.ImageData(dimensions=(width, height, 1))
    picture.point_data['rgba'] = np.flipud(rgba).reshape(-1, 4)
    picture.save(out)


if __name__ == '__main__':
    source, out, width, height = sys.argv[1:5]
    save(render(source, int(width), int(height)), out)
