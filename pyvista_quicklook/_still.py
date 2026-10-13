"""Render a mesh file to a PNG on a transparent background, cropped to the dataset."""

from __future__ import annotations

import copy
import os
import sys

import numpy as np
import pyvista as pv

if __package__:
    from ._scene_export import NOT_A_MESH_STATUS
    from ._scene_export import NotAMeshError
    from ._scene_export import apply_theme
    from ._scene_export import read_for_preview
    from ._scene_export import summary
else:
    from _scene_export import NOT_A_MESH_STATUS
    from _scene_export import NotAMeshError
    from _scene_export import apply_theme
    from _scene_export import read_for_preview
    from _scene_export import summary

# Rendered this many times larger, then averaged down, so edges are smooth.
SCALE = 2
# Space left around the dataset, as a share of its side; Camera.swift's frameMargin matches.
MARGIN = 0.05
MULTI_SAMPLES = 8


def srgb_to_linear(c: np.ndarray) -> np.ndarray:
    """Decode sRGB values in [0, 1] to linear light."""
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def linear_to_srgb(c: np.ndarray) -> np.ndarray:
    """Encode linear-light values in [0, 1] as sRGB."""
    return np.where(c <= 0.0031308, c * 12.92, 1.055 * c ** (1 / 2.4) - 0.055)


def compose(image: np.ndarray, scale: int = SCALE) -> np.ndarray:
    """Return a premultiplied RGBA render over black as straight RGBA, cropped square."""
    rows, columns = np.nonzero(image[..., 3])
    if rows.size == 0:
        message = 'the dataset draws nothing'
        raise NotAMeshError(message)
    content = image[rows.min() : rows.max() + 1, columns.min() : columns.max() + 1] / 255
    content[..., :3] = srgb_to_linear(content[..., :3])
    height, width = content.shape[:2]
    side = max(height, width)
    side += 2 * int(np.ceil(MARGIN * side))
    side += -side % scale
    square = np.zeros((side, side, 4))
    top, left = (side - height) // 2, (side - width) // 2
    square[top : top + height, left : left + width] = content

    # Average premultiplied linear channels, then divide out coverage once at the final size.
    small = square.reshape(side // scale, scale, side // scale, scale, 4).mean(axis=(1, 3))
    alpha = small[..., 3:]
    straight = np.divide(small[..., :3], alpha, out=np.zeros_like(small[..., :3]), where=alpha > 0)
    rgba = np.concatenate([linear_to_srgb(straight.clip(0, 1)), alpha], axis=-1)
    return np.round(rgba * 255).astype(np.uint8)


def render(source: str, width: int, height: int) -> np.ndarray:
    """Return the dataset drawn on a transparent background, cropped square, as RGBA bytes."""
    dataset, _ = read_for_preview(source)
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
    return compose(image)


def save(rgba: np.ndarray, out: str) -> None:
    """Write RGBA bytes, top row first, as a PNG."""
    height, width = rgba.shape[:2]
    picture = pv.ImageData(dimensions=(width, height, 1))
    picture.point_data['rgba'] = np.flipud(rgba).reshape(-1, 4)
    picture.save(out)


def main() -> int:
    """Render the still named on the command line."""
    source, out, width, height = sys.argv[1:5]
    try:
        apply_theme(sys.argv[5] if len(sys.argv) > 5 else None)
        save(render(source, int(width), int(height)), out)
    except Exception as error:
        # The reason is shown in the Quick Look panel, where a traceback would fill it.
        if os.environ.get('PVQL_TRACEBACK'):
            raise
        if isinstance(error, NotAMeshError):
            sys.stderr.write(f'{error}\n')
            return NOT_A_MESH_STATUS
        sys.stderr.write(f'{summary(error)}\n')
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
