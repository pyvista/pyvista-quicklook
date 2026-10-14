"""Tests that the preview's camera moves as VTK's trackball camera moves PyVista's."""

from __future__ import annotations

import json
import math
from pathlib import Path
import subprocess
import sys

from conftest import compiled
import numpy as np
import pytest
import pyvista as pv

ROOT = Path(__file__).resolve().parent.parent

pytestmark = pytest.mark.skipif(sys.platform != 'darwin', reason='SceneKit is macOS only')

# A camera off to one side, so no axis is special.
START = {'position': (3.0, -1.0, 2.0), 'target': (0.5, 0.25, -0.5), 'up': (0.0, 0.0, 1.0)}


@pytest.fixture(scope='session')
def probe():
    """Compile the camera probe against the extension's own camera code."""
    sources = (
        ROOT / 'macos' / 'Shared' / 'Camera.swift',
        ROOT / 'macos' / 'Tools' / 'CameraProbe.swift',
    )
    return compiled('CameraProbe', sources, ('-parse-as-library', '-framework', 'SceneKit'))


@pytest.fixture
def moved(probe):
    """Return a function moving the camera from START as told, returning where it ends up."""

    def after(*moves, start=START):
        numbers = [str(v) for key in ('position', 'target', 'up') for v in start[key]]
        finished = subprocess.run(
            [str(probe), *numbers, *moves], capture_output=True, text=True, timeout=60, check=False
        )
        assert finished.returncode == 0, finished.stderr
        return {key: np.array(value) for key, value in json.loads(finished.stdout).items()}

    return after


def vtk_camera(start=START):
    """Return VTK's camera at the same start."""
    camera = pv.Camera()
    camera.position = start['position']
    camera.focal_point = start['target']
    camera.up = start['up']
    return camera


def assert_matches(moved, camera):
    """Assert the probe's camera is where VTK's is, pointing its up the same way."""
    np.testing.assert_allclose(moved['position'], camera.position, atol=1e-4)
    np.testing.assert_allclose(moved['target'], camera.focal_point, atol=1e-4)
    up = np.array(camera.up) / np.linalg.norm(camera.up)
    np.testing.assert_allclose(moved['up'] / np.linalg.norm(moved['up']), up, atol=1e-4)


@pytest.mark.parametrize('degrees', [10, -37.5, 180])
def test_azimuth_turns_as_vtk_does(moved, degrees):
    """Azimuth is VTK's: about the view up, through the focal point."""
    camera = vtk_camera()
    camera.Azimuth(degrees)
    assert_matches(moved(f'azimuth={degrees}'), camera)


@pytest.mark.parametrize('degrees', [10, -25, 60])
def test_elevation_lifts_as_vtk_does(moved, degrees):
    """Elevation is VTK's, with the view up made perpendicular again after it."""
    camera = vtk_camera()
    camera.Elevation(degrees)
    camera.OrthogonalizeViewUp()
    assert_matches(moved(f'elevation={degrees}'), camera)


@pytest.mark.parametrize('degrees', [15, -90])
def test_roll_spins_as_vtk_does(moved, degrees):
    """Roll is VTK's: about the line of sight, leaving the camera where it is."""
    camera = vtk_camera()
    camera.Roll(degrees)
    assert_matches(moved(f'roll={degrees}'), camera)


@pytest.mark.parametrize('factor', [1.1, 0.5, 3])
def test_dolly_approaches_as_vtk_does(moved, factor):
    """Dolly is VTK's: the distance to the focal point divided by the factor."""
    camera = vtk_camera()
    camera.Dolly(factor)
    assert_matches(moved(f'dolly={factor}'), camera)


def test_a_drag_rotates_by_vtks_sensitivity(moved):
    """A drag is VTK's Rotate: 20 degrees times the motion factor across the view."""
    camera = vtk_camera()
    width, height = 800, 600
    camera.Azimuth(40 * -20 / width * 10)
    camera.Elevation(-15 * -20 / height * 10)
    camera.OrthogonalizeViewUp()
    assert_matches(moved(f'rotate=40,-15,{width},{height}'), camera)


def test_a_wheel_notch_and_a_right_drag_zoom_by_vtks_factors(moved):
    """A notch dollies as VTK's wheel does, a right drag by 1.1 to the motion over half a view."""
    camera = vtk_camera()
    camera.Dolly(1.1 ** (10 * 0.2 * 2))
    assert_matches(moved('wheel=2'), camera)
    camera = vtk_camera()
    camera.Dolly(1.1 ** (10 * 30 / 300))
    assert_matches(moved('drag=30,800,600'), camera)


def test_a_spin_rolls_by_the_angle_swept_about_the_centre(moved):
    """Control-dragging a quarter turn about the view's centre rolls the camera a quarter turn."""
    camera = vtk_camera()
    camera.Roll(90)
    assert_matches(moved('spin=500,300,400,400,800,600'), camera)


def test_a_pan_keeps_the_point_under_the_mouse_under_it(moved):
    """Dragging the width of the view moves the scene the width of the view, at the focal depth."""
    start = {'position': (0.0, 0.0, 4.0), 'target': (0.0, 0.0, 0.0), 'up': (0.0, 1.0, 0.0)}
    width, height, fov = 800, 600, 30
    span = 2 * 4 * math.tan(math.radians(fov / 2))
    after = moved(f'pan=800,0,{width},{height},{fov},1', start=start)
    np.testing.assert_allclose(after['position'], [-span * 800 / 600, 0, 4], atol=1e-4)
    np.testing.assert_allclose(after['target'], [-span * 800 / 600, 0, 0], atol=1e-4)
    # With the view angle across the width instead, the same drag moves the scene its width.
    across = moved(f'pan=800,0,{width},{height},{fov},0', start=start)
    np.testing.assert_allclose(across['target'], [-span, 0, 0], atol=1e-4)
    # Dragged up the height of the view, the scene follows upward.
    upward = moved(f'pan=0,600,{width},{height},{fov},1', start=start)
    np.testing.assert_allclose(upward['target'], [0, -span, 0], atol=1e-4)
