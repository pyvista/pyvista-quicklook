"""Tests that the scene actually draws, using the same camera the panel uses."""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys

import numpy as np
import pytest
import pyvista as pv

from pyvista_quicklook import _scene_export as export_mod

ROOT = Path(__file__).resolve().parent.parent
TOOL = ROOT / 'build' / 'RenderScene'

pytestmark = pytest.mark.skipif(sys.platform != 'darwin', reason='SceneKit is macOS only')


@pytest.fixture(scope='session', autouse=True)
def render_tool():
    """Compile the off-screen renderer when the build has not already produced it."""
    if TOOL.exists():
        return TOOL
    TOOL.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            'swiftc',
            '-O',
            '-parse-as-library',
            str(ROOT / 'macos' / 'Shared' / 'Camera.swift'),
            str(ROOT / 'macos' / 'Tools' / 'RenderScene.swift'),
            '-o',
            str(TOOL),
            '-framework',
            'AppKit',
            '-framework',
            'SceneKit',
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return TOOL


def rendered(mesh, tmp_path, name='scene', side=192):
    """Export a dataset, render it off screen, and return the pixels."""
    source = tmp_path / f'{name}.vtk'
    mesh.save(source)
    scene = tmp_path / f'{name}.ply'
    export_mod.export(str(source), str(scene), 2_000_000, 20_000)

    image = tmp_path / f'{name}.png'
    finished = subprocess.run(
        [str(TOOL), str(scene), str(image), str(side)],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert finished.returncode == 0, finished.stderr
    pixels = np.asarray(pv.read(image).active_scalars).reshape(side, side, -1)
    return pixels[:, :, :3]


def covered(pixels):
    """Return the fraction of the frame that differs from the background in its corner."""
    background = pixels[0, 0]
    return float(np.mean(np.abs(pixels.astype(int) - background.astype(int)).sum(axis=2) > 12))


def test_a_surface_is_drawn(tmp_path):
    """A sphere fills a sensible share of the frame rather than nothing."""
    pixels = rendered(pv.Sphere(theta_resolution=16, phi_resolution=16), tmp_path)
    assert 0.10 < covered(pixels) < 0.95


def test_a_point_cloud_is_drawn(tmp_path):
    """Glyphed points reach the frame instead of rendering blank."""
    cloud = pv.PolyData(np.random.default_rng(0).random((400, 3)))
    assert covered(rendered(cloud, tmp_path, name='cloud')) > 0.05


def test_a_sparse_point_cloud_is_drawn(tmp_path):
    """Three points in a small space are still large enough to see."""
    cloud = pv.PolyData(np.array([[0.0, 0, 0], [0.004, 0.002, 0], [0.002, 0.004, 0.003]]))
    assert covered(rendered(cloud, tmp_path, name='sparse')) > 0.003


def test_lines_are_drawn(tmp_path):
    """Tubed lines reach the frame instead of rendering blank."""
    points = np.array([[0.0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 1]])
    line = pv.PolyData(points, lines=np.array([4, 0, 1, 2, 3]))
    assert covered(rendered(line, tmp_path, name='line')) > 0.005


def test_slices_are_visible_from_the_default_camera(tmp_path):
    """A sliced volume shows from where the camera starts, not only after a rotation."""
    volume = pv.ImageData(dimensions=(20, 20, 20))
    volume['t'] = np.arange(volume.n_points, dtype=float)
    assert covered(rendered(volume, tmp_path, name='slices')) > 0.05


def test_a_surface_facing_away_is_drawn(tmp_path):
    """A plane whose normal points away from the camera is still drawn, and lit."""
    plane = pv.Plane(direction=(0, 0, -1))
    assert covered(rendered(plane, tmp_path, name='away')) > 0.02


def test_scalars_reach_the_render_as_colour(tmp_path):
    """A coloured dataset draws in more than one hue."""
    pixels = rendered(pv.Sphere(theta_resolution=16, phi_resolution=16).elevation(), tmp_path)
    hues = np.unique(pixels.reshape(-1, 3), axis=0)
    assert len(hues) > 50


def test_a_scene_without_geometry_is_refused(tmp_path):
    """An unreadable scene fails loudly rather than writing a blank picture."""
    finished = subprocess.run(
        [str(TOOL), str(tmp_path / 'absent.ply'), str(tmp_path / 'out.png')],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert finished.returncode != 0
    assert 'no geometry' in finished.stderr
