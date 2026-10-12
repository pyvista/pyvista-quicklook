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


SOURCES = [
    ROOT / 'macos' / 'Shared' / 'Camera.swift',
    ROOT / 'macos' / 'Tools' / 'RenderScene.swift',
    ROOT / 'macos' / 'Tools' / 'RenderScene-Info.plist',
]


@pytest.fixture(scope='session', autouse=True)
def render_tool():
    """Compile the off-screen renderer unless a build at least as new as its sources exists."""
    if TOOL.exists() and TOOL.stat().st_mtime >= max(path.stat().st_mtime for path in SOURCES):
        return TOOL
    TOOL.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            'swiftc',
            '-O',
            '-parse-as-library',
            str(SOURCES[0]),
            str(SOURCES[1]),
            '-o',
            str(TOOL),
            '-framework',
            'AppKit',
            '-framework',
            'SceneKit',
            # Blends colours as the panel does; build.sh embeds the same property list.
            '-Xlinker',
            '-sectcreate',
            '-Xlinker',
            '__TEXT',
            '-Xlinker',
            '__info_plist',
            '-Xlinker',
            str(SOURCES[2]),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return TOOL


def rendered(mesh, tmp_path, name='scene', side=192):
    """Export a dataset, render it off screen, and return the pixels, top row first."""
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
    # pv.read puts an image's bottom row first.
    pixels = np.asarray(pv.read(image).active_scalars).reshape(side, side, -1)
    return np.flipud(pixels[:, :, :3])


def covered(pixels):
    """Return the fraction of the frame that differs from the background in its corner."""
    background = pixels[0, 0]
    return float(np.mean(np.abs(pixels.astype(int) - background.astype(int)).sum(axis=2) > 12))


def test_a_surface_is_drawn(tmp_path):
    """A sphere fills a sensible share of the frame rather than nothing."""
    pixels = rendered(pv.Sphere(theta_resolution=16, phi_resolution=16), tmp_path)
    assert 0.10 < covered(pixels) < 0.95


def test_a_plain_surface_is_drawn_in_the_theme_colour(tmp_path):
    """A mesh without scalars is drawn in a blue hue, as PyVista's theme colour is."""
    pixels = rendered(pv.Sphere(theta_resolution=16, phi_resolution=16), tmp_path)
    lit = pixels.reshape(-1, 3).astype(int)
    lit = lit[lit.sum(axis=1) > 60]
    assert (lit[:, 2] - lit[:, 0]).mean() > 20


def lit_percentiles(pixels):
    """Return the 10th, 50th and 90th percentile colours of the pixels that are not background."""
    lit = pixels.reshape(-1, 3).astype(float)
    return np.percentile(lit[lit.sum(axis=1) > 30], [10, 50, 90], axis=0)


@pytest.fixture(scope='module')
def warmed_up():
    """Discard one off-screen render, since the first in a process can come out blank on macOS."""
    plotter = pv.Plotter(off_screen=True)
    plotter.screenshot()
    plotter.close()


def cell_coloured_cube():
    """Return a cube whose triangles are coloured by cell, which shows where the light falls."""
    cube = pv.Cube().triangulate().subdivide(3)
    cube.cell_data['id'] = np.arange(cube.n_cells) % 7
    cube.set_active_scalars('id')
    return cube


@pytest.mark.usefixtures('warmed_up')
@pytest.mark.parametrize(
    'mesh',
    [
        pv.Sphere(theta_resolution=64, phi_resolution=64),
        pv.Sphere(theta_resolution=64, phi_resolution=64).elevation(),
        cell_coloured_cube(),
    ],
    ids=['plain', 'elevation', 'cells'],
)
def test_colours_match_what_pyvista_draws(tmp_path, mesh):
    """The scene is lit and shaded as PyVista draws it, so its colours match a PyVista render."""
    plotter = pv.Plotter(off_screen=True, window_size=[192, 192])
    plotter.background_color = 'black'
    plotter.add_mesh(mesh, show_scalar_bar=False)
    expected = plotter.screenshot(return_img=True)[..., :3]
    plotter.close()
    difference = lit_percentiles(rendered(mesh, tmp_path)) - lit_percentiles(expected)
    assert np.abs(difference).mean() < 15


def highlight(pixels):
    """Return the brightest tenth's offset from a sphere's centre, in radii, x right and y up."""
    brightness = pixels.astype(float).sum(axis=2)
    lit = brightness > 30
    rows, columns = np.nonzero(lit)
    radius = np.sqrt(lit.sum() / np.pi)
    bright = brightness[lit] >= np.percentile(brightness[lit], 90)
    return np.array(
        [
            (columns[bright].mean() - columns.mean()) / radius,
            (rows.mean() - rows[bright].mean()) / radius,
        ]
    )


@pytest.mark.usefixtures('warmed_up')
def test_the_light_falls_where_pyvista_puts_it(tmp_path):
    """The lights sit around the camera as PyVista's do, so the highlight lands where it does."""
    sphere = pv.Sphere(theta_resolution=64, phi_resolution=64)
    plotter = pv.Plotter(off_screen=True, window_size=[192, 192])
    plotter.background_color = 'black'
    plotter.add_mesh(sphere)
    expected = plotter.screenshot(return_img=True)[..., :3]
    plotter.close()
    assert np.allclose(highlight(rendered(sphere, tmp_path)), highlight(expected), atol=0.15)


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
