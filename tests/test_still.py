"""Tests for how a still is composed from a render taken over black."""

from __future__ import annotations

import numpy as np
import pytest

from pyvista_quicklook import _still as still


def srgb_byte(linear):
    """Return the sRGB byte that encodes a linear-light value."""
    return round(float(still.linear_to_srgb(np.array(linear))) * 255)


@pytest.fixture
def render():
    """Return a render over black: a white block, and a grey edge column half covered."""
    image = np.zeros((40, 40, 4), dtype=np.uint8)
    image[10:20, 5:25] = 255
    # Grey of linear 0.2 at half coverage, premultiplied in linear light as multisampling does.
    image[10:20, 25, :3] = srgb_byte(0.1)
    image[10:20, 25, 3] = 128
    return image


def test_a_still_is_cropped_square_with_a_margin(render):
    """The dataset is cut out of the frame and centred in a square, a margin from each edge."""
    rgba = still.compose(render, scale=1)
    # 21 columns of content and a margin of ceil(0.05 * 21) = 2 on each side.
    assert rgba.shape == (25, 25, 4)
    rows, columns = np.nonzero(rgba[..., 3])
    assert (rows.min(), rows.max(), columns.min(), columns.max()) == (7, 16, 2, 22)


def test_edges_are_divided_out_of_the_black_in_linear_light(render):
    """A half-covered edge keeps its own colour, neither darkened by the black nor brightened."""
    rgba = still.compose(render, scale=1)
    edge = rgba[7, 22]
    assert edge[3] == 128
    assert np.abs(edge[:3].astype(int) - srgb_byte(0.2)).max() <= 1
    assert (rgba[7, 2, :3] == 255).all()


def test_a_still_is_scaled_down_by_averaging_coverage(render):
    """Rendered larger, a still is averaged down to its final size with straight colours."""
    rgba = still.compose(render, scale=2)
    # 25 rounded up to 26, then halved.
    assert rgba.shape == (13, 13, 4)
    assert (rgba[rgba[..., 3] == 255][:, :3] == 255).all()
    partial = (rgba[..., 3] > 0) & (rgba[..., 3] < 255)
    assert partial.any()
    assert (rgba[partial][:, :3] >= srgb_byte(0.2) - 1).all()


def test_a_render_with_nothing_drawn_is_not_a_mesh():
    """A frame left empty is reported as nothing to show rather than written blank."""
    with pytest.raises(still.NotAMeshError, match='draws nothing'):
        still.compose(np.zeros((8, 8, 4), dtype=np.uint8))
