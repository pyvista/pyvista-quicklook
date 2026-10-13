"""Tests for the exporter's point budget, colouring, and surface handling."""

from __future__ import annotations

import numpy as np
import pytest
import pyvista as pv

from pyvista_quicklook import _scene_export as export_mod


def exported(mesh, tmp_path, max_points=0, name='in.vtp'):
    """Export a dataset under a point budget and read the PLY back."""
    source = tmp_path / name
    mesh.save(source)
    destination = tmp_path / 'out.ply'
    export_mod.export(str(source), str(destination), max_points, 20_000)
    return pv.read(destination)


def test_a_cropped_image_is_thinned_within_its_own_extent(tmp_path):
    """An image whose extent starts past zero is strided over the indices it has."""
    image = pv.ImageData(dimensions=(300, 300, 1), offset=(100, 100, 0))
    assert image.extent == (100, 399, 100, 399, 0, 0)
    image['height'] = np.arange(image.n_points, dtype=float)
    scene = exported(image, tmp_path, max_points=2_000, name='in.vti')
    assert 0 < scene.n_points <= 2_000
    assert 'RGB' in scene.point_data


def test_a_list_colormap_spans_the_ramp(tmp_path, monkeypatch):
    """A theme colormap given as colours is interpolated from the first to the last."""
    monkeypatch.setattr(pv.global_theme, 'cmap', ['red', 'blue'])
    assert len(export_mod.ramp()) == export_mod.RAMP_SIZE
    sphere = pv.Sphere(theta_resolution=8, phi_resolution=8)
    sphere['z'] = sphere.points[:, 2]
    scene = exported(sphere, tmp_path)
    colours = scene['RGB']
    assert tuple(colours[np.argmin(scene.points[:, 2])]) == (255, 0, 0)
    assert tuple(colours[np.argmax(scene.points[:, 2])]) == (0, 0, 255)


def test_a_stripped_surface_keeps_its_faces(tmp_path):
    """Triangle strips are faces to draw, not loose points to glyph."""
    sphere = pv.Sphere(theta_resolution=8, phi_resolution=8)
    stripped = sphere.strip()
    assert stripped.n_faces == 0
    scene = exported(stripped, tmp_path)
    assert scene.n_faces == sphere.n_faces
    assert scene.n_points == sphere.n_points


@pytest.mark.parametrize('budget', [3_000, 600])
def test_cell_scalars_fit_the_budget_once_split(tmp_path, budget):
    """A mesh coloured by cells is decimated so the split points stay within the budget."""
    sphere = pv.Sphere(theta_resolution=40, phi_resolution=40)
    sphere.cell_data['part'] = np.arange(sphere.n_cells, dtype=float)
    assert 3 * sphere.n_faces > budget
    scene = exported(sphere, tmp_path, max_points=budget)
    assert scene.n_points <= budget * 1.05
    assert scene.n_faces > 0


def test_an_open_surface_fits_the_budget_once(tmp_path):
    """An open polygonal surface is written once, so it is decimated to the budget itself."""
    plane = pv.Plane(i_resolution=60, j_resolution=60).triangulate()
    plane['height'] = plane.points[:, 0]
    assert plane.n_points > 2_000
    scene = exported(plane, tmp_path, max_points=2_000)
    assert 2_000 * 0.5 < scene.n_points <= 2_000 * 1.05
    assert scene.n_faces > 0


def test_a_closed_surface_within_the_budget_is_left_alone(tmp_path):
    """A closed surface below the budget keeps every point."""
    sphere = pv.Sphere(theta_resolution=20, phi_resolution=20)
    scene = exported(sphere, tmp_path, max_points=sphere.n_points)
    assert scene.n_points == sphere.n_points


def test_a_polydata_keeps_only_its_own_arrays():
    """A PolyData is taken as it is, without VTK's original id arrays."""
    sphere = pv.Sphere()
    surface = export_mod.to_surface(sphere)
    assert surface is sphere
    assert surface.array_names == sphere.array_names
    grid = pv.ImageData(dimensions=(3, 3, 3)).cast_to_unstructured_grid()
    assert set(export_mod.to_surface(grid).array_names) == set(grid.array_names)


def test_only_bookkeeping_names_are_stepped_over():
    """An array named with VTK's prefix but not reserved by it colours the mesh."""
    sphere = pv.Sphere(theta_resolution=8, phi_resolution=8)
    sphere['vtkIds'] = np.arange(sphere.n_points, dtype=float)
    assert export_mod.choose_scalars(sphere).active_scalars_name == 'vtkIds'
    sphere.cell_data['vtkGhostType'] = np.zeros(sphere.n_cells, np.uint8)
    sphere.set_active_scalars('vtkGhostType')
    assert export_mod.choose_scalars(sphere).active_scalars_name == 'vtkIds'


def test_a_nan_value_takes_the_nan_colour(tmp_path):
    """A NaN scalar is drawn in the theme's colour for NaN, not at the bottom of the ramp."""
    sphere = pv.Sphere(theta_resolution=8, phi_resolution=8)
    values = sphere.points[:, 2].copy()
    values[0] = np.nan
    sphere['z'] = values
    scene = exported(sphere, tmp_path)
    nan_colour = pv.global_theme.nan_color.int_rgb
    assert tuple(scene['RGB'][0]) == tuple(nan_colour)
    assert tuple(scene['RGB'][np.nanargmin(values)]) == tuple(export_mod.ramp()[0])


def test_a_partitioned_dataset_counts_its_points():
    """The points of a partitioned dataset are counted over its partitions."""
    partitioned = pv.PartitionedDataSet([pv.Sphere(), pv.Cube()])
    assert export_mod.point_count(partitioned) == pv.Sphere().n_points + pv.Cube().n_points


def test_text_is_told_from_binary(tmp_path):
    """Printable bytes read as text, control characters and NUL do not."""
    text = tmp_path / 'notes.txt'
    text.write_text('solid thing\n' * 40)
    assert export_mod.looks_like_text(str(text))
    binary = tmp_path / 'blob.bin'
    binary.write_bytes(bytes(range(1, 32)) * 10)
    assert not export_mod.looks_like_text(str(binary))
    binary.write_bytes(b'abc\0def')
    assert not export_mod.looks_like_text(str(binary))
    assert not export_mod.looks_like_text(str(tmp_path / 'missing'))
