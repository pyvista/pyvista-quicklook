"""Tests for the exporter."""

from __future__ import annotations

import json
from pathlib import Path

import meshio
import numpy as np
import pytest
import pyvista as pv
from pyvista.core.utilities import _optional_formats as optional_formats
from pyvista.core.utilities import reader as readers
from pyvista.core.utilities import reader_registry
from pyvista_cad import examples as cad_examples

from pyvista_quicklook import _formats_table
from pyvista_quicklook import _scene_export as export_mod
from pyvista_quicklook import formats


def written(mesh_or_path, tmp_path, name='out.ply', **kwargs):
    """Export a dataset and read the resulting PLY back."""
    source = tmp_path / 'in.vtk'
    if isinstance(mesh_or_path, (str,)):
        source = mesh_or_path
    else:
        mesh_or_path.save(source)
    destination = tmp_path / name
    export_mod.export(
        str(source),
        str(destination),
        kwargs.pop('max_points', 2_000_000),
        **kwargs,
    )
    return pv.read(destination)


def coloured(mesh):
    """Return whether a PLY read back is coloured by scalars, in more than one colour."""
    return 'RGB' in mesh.array_names and len(np.unique(mesh['RGB'], axis=0)) > 1


def plain(mesh):
    """Return whether a PLY read back is drawn all in the theme's colour."""
    return 'RGB' in mesh.array_names and bool((mesh['RGB'] == pv.global_theme.color.int_rgb).all())


def test_a_surface_survives_with_its_faces(tmp_path):
    """An ordinary surface keeps its geometry."""
    out = written(pv.Sphere(theta_resolution=8, phi_resolution=8), tmp_path)
    assert out.n_points > 0
    assert out.n_faces > 0


def test_a_volume_becomes_its_boundary(tmp_path):
    """A volume is reduced to a surface rather than refused."""
    out = written(pv.Wavelet(), tmp_path)
    assert out.n_faces > 0


def test_a_point_cloud_is_written_as_bare_points(tmp_path):
    """Loose points are written as they are, for the scene to draw as round points."""
    cloud = pv.PolyData(np.random.default_rng(0).random((200, 3)))
    out = written(cloud, tmp_path, name='cloud.ply')
    assert out.n_faces == 0
    assert out.n_points == cloud.n_points
    assert style_of(tmp_path / 'cloud.ply')['primitive'] == 'points'


def test_a_point_set_is_written_as_points(tmp_path):
    """A PointSet, which has no cells at all, is drawn as points coloured by its scalars."""
    points = pv.PointSet(np.random.default_rng(0).random((50, 3)))
    points['height'] = points.points[:, 2]
    points.save(tmp_path / 'points.pv')
    out = tmp_path / 'points.ply'
    export_mod.export(str(tmp_path / 'points.pv'), str(out), 0)
    scene = pv.read(out)
    assert scene.n_points == 50
    assert scene.n_faces == 0
    assert style_of(out)['primitive'] == 'points'
    assert coloured(scene)


def bare_polyline():
    """Return a polyline carrying no data arrays of its own."""
    points = np.array([[0.0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 1]])
    return pv.PolyData(points, lines=np.array([4, 0, 1, 2, 3]))


def test_lines_become_folded_triangles(tmp_path):
    """Each segment is a triangle folded back on itself, which the scene draws as a line."""
    line = bare_polyline()
    assert line.n_faces == 0
    out = written(line, tmp_path, name='line.ply')
    assert out.n_faces == 3
    assert out.regular_faces.tolist() == [[0, 1, 0], [1, 2, 1], [2, 3, 2]]
    assert style_of(tmp_path / 'line.ply')['primitive'] == 'lines'


def test_line_cell_data_follows_each_segment():
    """A polyline's cell data is repeated on each segment, counted past any vertex cells."""
    points = np.array([[0.0, 0, 0], [1, 0, 0], [2, 0, 0], [3, 0, 0]])
    lines = pv.PolyData(points, lines=np.array([3, 0, 1, 2, 2, 2, 3]), verts=np.array([1, 0]))
    lines.cell_data['part'] = np.array([100.0, 7.0, 9.0])
    drawn = export_mod.segments(lines)
    assert drawn.cell_data['part'].tolist() == [7.0, 7.0, 9.0]


def test_single_point_lines_are_drawn_as_points(tmp_path):
    """Line cells of one point each make no segment, so the points are drawn instead."""
    dots = pv.PolyData(np.random.default_rng(0).random((3, 3)), lines=np.array([1, 0, 1, 1, 1, 2]))
    out = written(dots, tmp_path, name='dots.ply')
    assert out.n_points == 3
    assert style_of(tmp_path / 'dots.ply')['primitive'] == 'points'


def test_a_point_cloud_over_budget_is_thinned(tmp_path):
    """A cloud larger than the point budget keeps a spread of its points, still coloured."""
    cloud = pv.PolyData(np.random.default_rng(0).random((5000, 3)))
    cloud['h'] = cloud.points[:, 2]
    out = written(cloud, tmp_path, name='cloud.ply', max_points=500)
    assert 250 < out.n_points <= 500
    assert coloured(out)


def test_point_scalars_are_carried_as_colour(tmp_path):
    """A dataset with point scalars previews in colour."""
    mesh = pv.Sphere(theta_resolution=8, phi_resolution=8).elevation()
    out = written(mesh, tmp_path)
    assert coloured(out)


def test_cell_scalars_are_split_rather_than_averaged(tmp_path):
    """Cell scalars keep each face flat, which needs one set of points per cell."""
    mesh = pv.Sphere(theta_resolution=8, phi_resolution=8).triangulate()
    mesh.cell_data['region'] = (np.arange(mesh.n_cells) % 4).astype(float)
    mesh.set_active_scalars('region')
    out = written(mesh, tmp_path)
    assert coloured(out)
    assert out.n_points == mesh.n_cells * 3


def test_faces_keep_their_own_cell_scalars_beside_points_and_lines(tmp_path):
    """Loose points and lines are left out, and each face keeps its own value."""
    points = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [2, 0, 0], [3, 0, 0], [2, 1, 0]], float)
    mesh = pv.PolyData(points, verts=[1, 0], lines=[2, 0, 1], faces=[3, 0, 1, 2, 3, 3, 4, 5])
    # Were the loose cells' value to reach the faces, the ramp would span 0 to 5.
    mesh.cell_data['region'] = [5.0, 5.0, 0.0, 1.0]
    mesh.set_active_scalars('region')
    out = written(mesh, tmp_path)
    colours = {tuple(rgb) for rgb in np.asarray(out['RGB'])}
    assert colours == {tuple(export_mod.ramp()[0]), tuple(export_mod.ramp()[255])}


def test_a_dataset_without_scalars_is_drawn_in_the_theme_colour(tmp_path):
    """A file that asks for no colour is drawn in the theme's colour, as pyvista plot draws it."""
    mesh = pv.Sphere(theta_resolution=8, phi_resolution=8)
    assert mesh.active_scalars_name is None
    assert plain(written(mesh, tmp_path))


def test_filter_arrays_do_not_become_the_colour(tmp_path):
    """Points and lines carrying no arrays are drawn plain."""
    assert plain(written(bare_polyline(), tmp_path, name='line.ply'))

    cloud = pv.PolyData(np.random.default_rng(0).random((100, 3)))
    assert plain(written(cloud, tmp_path, name='cloud.ply'))


def test_a_line_keeps_its_own_scalars(tmp_path):
    """A line that does carry scalars is still coloured by them."""
    line = pv.Line(pointa=(0, 0, 0), pointb=(1, 1, 1), resolution=10)
    assert line.active_scalars_name == 'Distance'
    assert coloured(written(line, tmp_path, name='line.ply'))


def test_normals_are_not_mistaken_for_scalars(tmp_path):
    """A mesh carrying only normals is drawn plain."""
    mesh = pv.Sphere(theta_resolution=8, phi_resolution=8).compute_normals()
    mesh.set_active_scalars(None)
    assert plain(written(mesh, tmp_path))


def ghosted(normals_name):
    """Return a sphere whose active scalars are VTK's ghost array, ahead of normals and regions."""
    mesh = pv.Sphere(theta_resolution=8, phi_resolution=8).triangulate()
    mesh.point_data.set_array(mesh.point_data.pop('Normals'), normals_name)
    mesh.point_data.active_normals_name = normals_name
    mesh.cell_data['vtkGhostType'] = np.zeros(mesh.n_cells, np.uint8)
    mesh.cell_data['region'] = (np.arange(mesh.n_cells) % 3).astype(float)
    mesh.set_active_scalars('vtkGhostType')
    return mesh


@pytest.mark.parametrize('normals_name', ['Normals', 'n'])
def test_vtk_bookkeeping_arrays_are_stepped_over(tmp_path, normals_name):
    """An active vtk array yields to a real one, not to the normals, rather than breaking."""
    assert coloured(written(ghosted(normals_name), tmp_path))


@pytest.mark.parametrize('normals_name', ['Normals', 'n'])
def test_a_selectively_read_file_steps_over_normals(tmp_path, normals_name):
    """A .pv file read array by array is coloured by a real array, not by its normals."""
    source = tmp_path / 'in.pv'
    ghosted(normals_name).save(source)
    assert coloured(written(str(source), tmp_path))


def test_the_plain_colour_follows_the_theme(tmp_path, monkeypatch):
    """A mesh without scalars takes the theme's colour, whatever the theme is."""
    monkeypatch.setattr(pv.global_theme, 'color', 'red')
    out = written(pv.Sphere(theta_resolution=8, phi_resolution=8), tmp_path)
    assert (out['RGB'] == (255, 0, 0)).all()


def test_the_result_is_centred(tmp_path):
    """Coordinates are moved to the origin, so distant datasets keep their precision."""
    mesh = pv.Sphere(theta_resolution=8, phi_resolution=8)
    mesh.points += 1.0e7
    out = written(mesh, tmp_path)
    assert np.allclose(out.center, 0, atol=max(1.0, out.length * 1e-3))


def test_an_empty_dataset_is_reported(tmp_path):
    """A dataset with nothing in it says so instead of writing an empty scene."""
    with pytest.raises(export_mod.NotAMeshError, match='no surface'):
        written(pv.PolyData(), tmp_path)


def test_empty_blocks_are_skipped(tmp_path):
    """Missing blocks and empty nested composites leave the other blocks to preview."""
    blocks = pv.MultiBlock(
        [pv.MultiBlock([pv.Sphere(), None]), None, pv.MultiBlock(), pv.MultiBlock([None])]
    )
    assert export_mod.point_count(blocks) == pv.Sphere().n_points
    source = tmp_path / 'in.vtm'
    blocks.save(source)
    assert written(str(source), tmp_path).n_points > 0


def test_a_composite_of_empty_blocks_is_reported(tmp_path):
    """A composite with no blocks at all is reported as empty."""
    blocks = pv.MultiBlock([None, pv.MultiBlock([None])])
    assert export_mod.point_count(blocks) == 0
    assert export_mod.to_surface(blocks).n_points == 0


def test_the_ramp_matches_viridis_ends():
    """The built-in colour table spans viridis from dark blue to yellow."""
    table = export_mod.ramp()
    assert table.shape == (256, 3)
    assert tuple(table[0]) == (68, 1, 84)
    assert tuple(table[-1]) == (253, 231, 37)


def test_large_images_are_strided_to_the_budget(tmp_path):
    """A structured dataset is thinned on its lattice rather than decimated."""
    image = pv.ImageData(dimensions=(400, 400, 1))
    image['height'] = np.arange(image.n_points, dtype=float)
    image.save(tmp_path / 'in.vti')
    out = tmp_path / 'out.ply'
    export_mod.export(str(tmp_path / 'in.vti'), str(out), 10_000)
    scene = pv.read(out)
    assert scene.n_points == 100 * 100
    assert np.unique(scene.points[:, 0]).size == 100
    assert 'RGB' in scene.point_data


def inside_the_bounds(scene):
    """Return the share of points that lie strictly inside the scene's bounding box."""
    points = np.asarray(scene.points)
    low, high = points.min(axis=0), points.max(axis=0)
    on_a_face = np.isclose(points, low) | np.isclose(points, high)
    return float(np.mean(~on_a_face.any(axis=1)))


def test_surfaces_are_written_once(tmp_path):
    """An open surface is written once; the viewer draws both sides, as PyVista does."""
    out = tmp_path / 'out.ply'
    image = pv.ImageData(dimensions=(20, 20, 1))
    image.save(tmp_path / 'slice.vti')
    export_mod.export(str(tmp_path / 'slice.vti'), str(out), 2_000_000)
    assert pv.read(out).n_points == image.n_points
    pv.Plane().save(tmp_path / 'plane.vtp')
    export_mod.export(str(tmp_path / 'plane.vtp'), str(out), 2_000_000)
    plane = pv.read(out)
    assert plane.n_faces == pv.Plane().triangulate().n_faces
    assert (np.asarray(plane.point_data['Normals'])[:, 2] > 0).all()
    pv.Sphere().save(tmp_path / 'sphere.vtp')
    export_mod.export(str(tmp_path / 'sphere.vtp'), str(out), 2_000_000)
    assert pv.read(out).n_faces == pv.Sphere().n_faces


def test_volumes_with_scalars_are_cut_into_slices(tmp_path):
    """A volume with scalars is shown as three slices through its centre, coloured."""
    volume = pv.ImageData(dimensions=(30, 30, 30))
    volume['t'] = np.arange(volume.n_points, dtype=float)
    volume.save(tmp_path / 'in.vti')
    out = tmp_path / 'out.ply'
    export_mod.export(str(tmp_path / 'in.vti'), str(out), 2_000_000)
    scene = pv.read(out)
    assert scene.n_points == 3 * 30 * 30
    assert inside_the_bounds(scene) > 0.5
    assert 'RGB' in scene.point_data


def test_volume_cell_data_is_drawn_flat_on_the_slices(tmp_path):
    """Cell data on a volume reaches the slices and is drawn one colour per cell."""
    volume = pv.ImageData(dimensions=(30, 30, 30))
    volume.cell_data['c'] = np.arange(volume.n_cells, dtype=float)
    volume.save(tmp_path / 'in.vti')
    out = tmp_path / 'out.ply'
    export_mod.export(str(tmp_path / 'in.vti'), str(out), 2_000_000)
    scene = pv.read(out)
    assert scene.n_points > 2 * 3 * 30 * 30
    assert 'RGB' in scene.point_data


@pytest.mark.network
@pytest.mark.parametrize(
    'download',
    [
        'download_beach',  # .nrrd
        'download_head',  # .mhd
        'download_chest',  # .mha
        'download_brain_atlas_with_sides',  # .nii.gz
        'download_t3_grid_0',  # .mnc
    ],
)
def test_medical_images_are_drawn(tmp_path, download):
    """A medical image from PyVista's examples is coloured; a volume is sliced through."""
    source = getattr(pv.examples.downloads, download)(load=False)
    out = tmp_path / 'out.ply'
    export_mod.export(str(source), str(out), 2_000_000)
    scene = pv.read(out)
    assert scene.n_faces > 0
    assert 'RGB' in scene.point_data
    if min(pv.read(source).dimensions) > 1:
        # Slices run through the volume; its outer surface would leave nothing inside.
        assert inside_the_bounds(scene) > 0.5


def test_volume_skin_is_kept_whole_when_it_fits(tmp_path):
    """A volume with nothing to colour shows its outer surface, whole while it fits."""
    volume = pv.ImageData(dimensions=(60, 60, 60))
    volume.save(tmp_path / 'in.vti')
    out = tmp_path / 'out.ply'
    export_mod.export(str(tmp_path / 'in.vti'), str(out), 100_000)
    # Six faces of 60 x 60 points; striding the volume itself would leave ~5 000.
    assert 20_000 <= pv.read(out).n_points <= 6 * 60 * 60
    export_mod.export(str(tmp_path / 'in.vti'), str(out), 5_000)
    assert pv.read(out).n_points <= 5_000


def test_partitioned_datasets_are_drawn_as_one_surface(tmp_path):
    """The partitions of a .vtpd or VTKHDF file are combined, as blocks are."""
    pv.PartitionedDataSet([pv.Sphere(), pv.Cube()]).save(tmp_path / 'in.vtpd')
    out = tmp_path / 'out.ply'
    export_mod.export(str(tmp_path / 'in.vtpd'), str(out), 2_000_000)
    assert pv.read(out).n_faces == pv.Sphere().n_faces + 12


def test_step_models_are_drawn(tmp_path):
    """A STEP model is tessellated by pyvista-cad, read back as glTF, and drawn."""
    out = tmp_path / 'out.ply'
    export_mod.export(str(cad_examples.bracket_step_path()), str(out), 2_000_000)
    assert pv.read(out).n_faces > 0


def test_iges_models_are_drawn(tmp_path):
    """An IGES model is tessellated by cascadio, read back as glTF, and drawn."""
    out = tmp_path / 'out.ply'
    export_mod.export(cad_examples.downloads.iges_impeller_path(), str(out), 2_000_000)
    assert pv.read(out).n_faces > 0


def test_unreadable_iges_files_are_reported(tmp_path):
    """An IGES file cascadio transfers no shapes from fails with a reason."""
    source = tmp_path / 'broken.igs'
    source.write_text('nonsense')
    with pytest.raises(export_mod.NotAMeshError, match='no shapes could be read'):
        export_mod.export(str(source), str(tmp_path / 'out.ply'), 2_000_000)


@pytest.mark.parametrize('extension', ['.h5m', '.hmf', '.med', '.xmf'])
def test_hdf5_meshes_are_drawn(tmp_path, extension):
    """The meshio formats stored in HDF5 are read with h5py."""
    sphere = pv.Sphere()
    source = tmp_path / f'in{extension}'
    meshio.write(source, meshio.Mesh(sphere.points, [('triangle', sphere.regular_faces)]))
    out = tmp_path / 'out.ply'
    export_mod.export(str(source), str(out), 2_000_000)
    assert pv.read(out).n_faces == sphere.n_faces


def test_dxf_drawings_are_drawn_as_lines(tmp_path):
    """A DXF drawing holds only lines, written as folded triangles the scene draws as lines."""
    out = tmp_path / 'out.ply'
    export_mod.export(str(cad_examples.drawing_dxf_path()), str(out), 2_000_000)
    scene = pv.read(out)
    assert scene.n_faces > 0
    assert (scene.regular_faces[:, 0] == scene.regular_faces[:, 2]).all()


def test_3mf_assemblies_are_drawn_as_one_surface(tmp_path):
    """The objects of a 3MF file are combined, as blocks are."""
    out = tmp_path / 'out.ply'
    export_mod.export(str(cad_examples.assembly_3mf_path()), str(out), 2_000_000)
    assert pv.read(out).n_faces > 0


MESHIO_SURFACE_FORMATS = [
    '.avs',
    '.bdf',
    '.fem',
    '.mdpa',
    '.mesh',
    '.meshb',
    '.nas',
    '.off',
    '.tec',
    '.vol',
]


@pytest.mark.parametrize('ext', MESHIO_SURFACE_FORMATS)
def test_meshio_surface_formats_are_drawn(tmp_path, ext):
    """A surface written in a meshio format is read back through meshio and drawn."""
    source = tmp_path / f'mesh{ext}'
    pv.save_meshio(source, pv.Sphere().triangulate())
    out = tmp_path / 'out.ply'
    export_mod.export(str(source), str(out), 2_000_000)
    assert pv.read(out).n_faces == pv.Sphere().n_faces


@pytest.mark.parametrize('ext', ['.pv', '.zvtk'])
def test_zstandard_files_are_drawn_with_their_scalars(tmp_path, ext):
    """A Zstandard-compressed file written by pyvista-zstd is read back and coloured."""
    mesh = pv.Sphere()
    mesh['t'] = np.arange(mesh.n_points, dtype=float)
    if ext == '.zvtk':
        # pyvista-zstd warns that the .zvtk spelling is on its way out.
        with pytest.warns(FutureWarning):
            mesh.save(tmp_path / f'mesh{ext}')
    else:
        mesh.save(tmp_path / f'mesh{ext}')
    out = tmp_path / 'out.ply'
    export_mod.export(str(tmp_path / f'mesh{ext}'), str(out), 2_000_000)
    scene = pv.read(out)
    assert scene.n_faces == mesh.n_faces
    assert 'RGB' in scene.point_data


@pytest.mark.network
def test_abaqus_and_avs_share_inp(tmp_path):
    """An .inp is read as Abaqus when the AVS reader finds nothing in it, and vice versa."""
    abaqus = tmp_path / 'part.inp'
    pv.save_meshio(abaqus, pv.Sphere().triangulate())
    avs = pv.examples.downloads.download_cells_nd(load=False)
    for source, expected in ((abaqus, pv.Sphere().n_faces), (avs, None)):
        out = tmp_path / 'out.ply'
        export_mod.export(str(source), str(out), 2_000_000)
        faces = pv.read(out).n_faces
        assert faces == expected if expected else faces > 0


def test_gmsh_ascii_files_are_drawn(tmp_path):
    """A Gmsh 2.2 ASCII file, as written by Gmsh itself, is drawn."""
    source = Path(__file__).with_name('data') / 'box.msh'
    out = tmp_path / 'out.ply'
    export_mod.export(str(source), str(out), 2_000_000)
    assert pv.read(out).n_faces > 0


def test_flac3d_grids_are_drawn(tmp_path):
    """FLAC3D holds volume cells only, so their outer surface is drawn."""
    source = tmp_path / 'zones.f3grid'
    pv.save_meshio(source, pv.ImageData(dimensions=(4, 4, 4)).triangulate())
    out = tmp_path / 'out.ply'
    export_mod.export(str(source), str(out), 2_000_000)
    assert pv.read(out).n_faces > 0


def test_binary_gmsh_files_are_drawn(tmp_path):
    """A binary Gmsh file, the form meshio reads reliably, is drawn."""
    source = tmp_path / 'mesh.msh'
    pv.save_meshio(source, pv.Sphere().triangulate(), file_format='gmsh', binary=True)
    out = tmp_path / 'out.ply'
    export_mod.export(str(source), str(out), 2_000_000)
    assert pv.read(out).n_faces == pv.Sphere().n_faces


def test_a_failure_is_reported_in_one_line(tmp_path, capsys, monkeypatch):
    """What the panel is shown is a sentence, not a traceback."""
    source = tmp_path / 'not-a-mesh.vtu'
    source.write_text('nonsense')
    monkeypatch.setattr(
        'sys.argv',
        [
            'export',
            str(source),
            str(tmp_path / 'out.ply'),
            '--max-points',
            '0',
        ],
    )
    with pytest.warns(UserWarning, match='raised an error while reading'):
        assert export_mod.main() == export_mod.NOT_A_MESH_STATUS
    reported = capsys.readouterr().err.strip()
    assert len(reported.splitlines()) == 1
    assert len(reported) <= 240


def raise_import_error(*args, **kwargs):
    """Stand in for a reader whose module is not installed."""
    message = 'No module named no_such_reader'
    raise ImportError(message)


@pytest.mark.parametrize(
    ('trouble', 'expected'),
    [
        ('missing', FileNotFoundError),
        ('unreadable', PermissionError),
        ('no reader module', ImportError),
        ('cut off', (RuntimeError, ValueError)),
    ],
)
def test_troubles_other_than_the_format_are_ordinary_failures(
    tmp_path, monkeypatch, trouble, expected
):
    """A file that cannot be reached or read, or a binary one cut short, is not shown as text."""
    source = tmp_path / 'mesh.ply'
    pv.Sphere().save(source)
    assert not export_mod.looks_like_text(str(source))
    if trouble == 'missing':
        source.unlink()
    elif trouble == 'unreadable':
        source.chmod(0)
    elif trouble == 'no reader module':
        monkeypatch.setattr(export_mod.pv, 'read', raise_import_error)
    else:
        source.write_bytes(source.read_bytes()[:400])
    try:
        with pytest.raises(expected) as raised:
            export_mod.export(str(source), str(tmp_path / 'out.ply'), 0, 0)
    finally:
        if trouble == 'unreadable':
            source.chmod(0o644)
    assert not isinstance(raised.value, export_mod.NotAMeshError)


def test_text_that_is_not_a_mesh_is_shown_as_text(tmp_path):
    """Text with a mesh extension, empty or unreadable as a mesh, is handed back as text."""
    empty = tmp_path / 'empty.vtu'
    empty.write_text('<?xml version="1.0"?><VTKFile type="UnstructuredGrid"></VTKFile>')
    assert export_mod.looks_like_text(str(empty))
    # The readers say what they made of each file, which is what makes it text.
    unreadable = 'raised an error while reading'
    no_surface = pytest.raises(export_mod.NotAMeshError, match='no surface')
    with pytest.warns(UserWarning, match=unreadable), no_surface:
        export_mod.export(str(empty), str(tmp_path / 'out.ply'), 0, 0)
    table = tmp_path / 'table.dat'
    table.write_text('x y z\n1 2 3\n')
    with pytest.warns(UserWarning, match=unreadable), pytest.raises(export_mod.NotAMeshError):
        export_mod.export(str(table), str(tmp_path / 'out.ply'), 0, 0)


def test_only_a_file_that_holds_no_mesh_exits_as_not_a_mesh(tmp_path, capsys, monkeypatch):
    """A failure after the file was read is an ordinary failure, not a file to show as text."""
    source = tmp_path / 'sphere.vtp'
    pv.Sphere().save(source)

    def broken(*args):
        message = 'out of memory'
        raise MemoryError(message)

    monkeypatch.setattr(export_mod, 'drawable', broken)
    monkeypatch.setattr('sys.argv', ['export', str(source), str(tmp_path / 'out.ply')])
    assert export_mod.main() == 1
    assert capsys.readouterr().err.strip() == 'MemoryError: out of memory'


def readable_extensions() -> set[str]:
    """Return every extension this environment can read.

    A reader that lives in a package of its own is named in a table of PyVista's rather
    than registered, until a file asks for it.
    """
    reader_registry._ensure_entry_points()
    registered = {entry.extension for entry in reader_registry.registered_readers()}
    return (
        set(readers.CLASS_READERS)
        | registered
        | set(reader_registry._pending_ext_readers)
        | optional_formats._installed_extensions(optional_formats._READ)
        | set(meshio.extension_to_filetypes)
    )


def test_docs_tables_match_the_readers():
    """The docs' format tables are exactly what PyVista's reader tables generate."""
    page = (Path(__file__).parents[1] / 'doc' / 'formats.rst').read_text()
    section = page.split(_formats_table.START, 1)[1].split(_formats_table.END, 1)[0]
    assert section == _formats_table.section()


def test_format_table_accounts_for_every_readable_extension():
    """Each extension the environment reads is listed or deliberately unclaimed, and no other."""
    accounted = set(formats.FORMATS) | set(formats.UNCLAIMED) | set(formats.VIA_SYSTEM_TYPE)
    accounted |= set(formats.NATIVE_TYPES)
    assert set(formats.FORMATS).isdisjoint(formats.UNCLAIMED)
    assert set(formats.FORMATS).isdisjoint(formats.VIA_SYSTEM_TYPE)
    assert all(uti in formats.SYSTEM_TYPES for uti in formats.VIA_SYSTEM_TYPE.values())
    assert readable_extensions() == accounted


def missing_reader(ext: str) -> str | None:
    """Return why the environment cannot read an extension, or None when it can."""
    if ext not in readers.CLASS_READERS:
        return None if ext in readable_extensions() else 'no reader is registered'
    try:
        readers.CLASS_READERS[ext](f'/nonexistent/sample{ext}')
    except ImportError as error:
        return str(error).splitlines()[0]
    except Exception:  # noqa: BLE001
        return None  # the reader exists and merely dislikes the path
    return None


def test_every_default_extension_has_a_reader():
    """The installed environment can read every format that is claimed by default."""
    missing = {ext: why for ext in formats.default_extensions() if (why := missing_reader(ext))}
    assert missing == {}


def test_a_failed_selective_read_falls_back_and_says_so(tmp_path, capsys, monkeypatch):
    """A selective read that fails is reported, and the file is read whole instead."""
    source = tmp_path / 'sphere.vtp'
    pv.Sphere().save(source)

    def broken(path):
        message = 'private API moved'
        raise AttributeError(message)

    monkeypatch.setattr(export_mod, 'read_selectively', broken)
    dataset, header = export_mod.read_for_preview(str(source))
    assert dataset.n_points == pv.Sphere().n_points
    assert header is None
    assert 'selective read skipped: AttributeError: private API moved' in capsys.readouterr().err


def test_a_file_that_cannot_be_opened_does_not_look_like_text(tmp_path):
    """Text is only claimed for a file whose opening can be read."""
    assert not export_mod.looks_like_text(str(tmp_path / 'gone.dat'))


def style_of(path):
    """Return the style a scene's header names."""
    head = Path(path).read_bytes()[:2048]
    marker = b'\ncomment pvql style '
    start = head.index(marker) + len(marker)
    return json.loads(head[start : head.index(b'\n', start)])


@pytest.mark.parametrize(
    ('smooth', 'interpolation', 'style'),
    [(False, 'flat', 'flat'), (True, 'flat', 'smooth'), (False, 'phong', 'smooth')],
)
def test_the_scene_says_how_the_theme_shades(tmp_path, theme, smooth, interpolation, style):
    """The header names the shading add_mesh would use, and the PLY still reads."""
    theme.smooth_shading = smooth
    theme.lighting_params.interpolation = interpolation
    out = written(pv.Sphere(theta_resolution=8, phi_resolution=8), tmp_path)
    assert (tmp_path / 'out.ply').read_bytes().startswith(b'ply\nformat ')
    assert style_of(tmp_path / 'out.ply')['shading'] == style
    assert out.n_points > 0


def test_the_scene_carries_the_theme_lighting(tmp_path, theme):
    """The header holds the theme's lighting, which SceneKit then follows."""
    theme.lighting = False
    theme.lighting_params.ambient = 0.2
    theme.lighting_params.specular = 0.5
    theme.lighting_params.specular_power = 30
    written(pv.Sphere(theta_resolution=8, phi_resolution=8), tmp_path)
    assert style_of(tmp_path / 'out.ply') == {
        'shading': 'flat',
        'lighting': False,
        'ambient': 0.2,
        'diffuse': 1.0,
        'specular': 0.5,
        'specular_power': 30.0,
        'point_size': 5.0,
        'line_width': 2.0,
        'primitive': 'faces',
    }


def test_a_thin_themed_line_is_widened_to_the_minimum(tmp_path, theme):
    """A theme's line width below the minimum draws at the minimum; a wider one is kept."""
    theme.line_width = 0.5
    written(bare_polyline(), tmp_path, name='thin.ply')
    assert style_of(tmp_path / 'thin.ply')['line_width'] == export_mod.MIN_LINE_WIDTH
    theme.line_width = 4
    written(bare_polyline(), tmp_path, name='wide.ply')
    assert style_of(tmp_path / 'wide.ply')['line_width'] == 4.0


def test_a_named_theme_is_applied(theme):
    """A registered theme's name sets PyVista's theme, colormap included."""
    export_mod.apply_theme('paraview')
    assert theme.cmap == 'coolwarm'
    assert tuple(export_mod.ramp()[0]) == tuple(pv.LookupTable(cmap='coolwarm').values[0][:3])


def test_a_saved_theme_is_applied(tmp_path, theme):
    """A theme saved as JSON sets PyVista's theme."""
    saved = pv.themes.DocumentTheme()
    saved.color = 'red'
    saved.smooth_shading = True
    saved.save(tmp_path / 'theme.json')
    export_mod.apply_theme(str(tmp_path / 'theme.json'))
    out = written(pv.Sphere(theta_resolution=8, phi_resolution=8), tmp_path)
    assert (out['RGB'] == (255, 0, 0)).all()
    assert style_of(tmp_path / 'out.ply')['shading'] == 'smooth'


def test_no_theme_leaves_pyvista_own(theme):
    """Without a theme setting, PyVista's theme is left as it is."""
    theme.color = 'green'
    export_mod.apply_theme(None)
    assert theme.color == pv.Color('green')


def test_a_dataset_that_cannot_surface_itself_has_its_geometry_extracted():
    """A reader result without a surface filter of its own is drawn through extract_geometry."""

    class Odd:
        """A dataset with no surface filter, as some VTK wrappers come."""

        def extract_geometry(self):
            return pv.Sphere()

    assert export_mod.to_surface(Odd()).n_faces == pv.Sphere().n_faces


def test_a_file_with_points_but_nothing_to_draw_is_refused(tmp_path):
    """A mesh whose points carry no cells gives no surface, which is reported, not written."""
    grid = pv.UnstructuredGrid()
    grid.points = np.zeros((3, 3))
    grid.save(tmp_path / 'in.vtu')
    out = tmp_path / 'out.ply'
    # An XML file reads as text, so the file is shown as such rather than as a failure.
    with pytest.raises(export_mod.NotAMeshError, match='no surface to show'):
        export_mod.export(str(tmp_path / 'in.vtu'), str(out), 2_000_000)
    assert not out.exists()


def test_a_surface_over_the_point_budget_is_decimated(tmp_path):
    """A surface with more points than allowed is thinned to the budget, faces kept."""
    full = pv.Sphere(theta_resolution=64, phi_resolution=64)
    out = written(full, tmp_path, max_points=200)
    # Decimation lands near the budget, not exactly on it.
    assert 0 < out.n_points < 250 < full.n_points
    assert out.n_faces > 0


def binnable(kind):
    """Return a sphere, a 60:1 tube along z, or a flat sheet standing in the x-z plane."""
    if kind == 'sphere':
        return pv.Sphere(theta_resolution=200, phi_resolution=200)
    if kind == 'tube':
        tube = pv.Cylinder(height=60, radius=0.5, resolution=40, capping=False).triangulate()
        return tube.subdivide(4)
    return pv.Plane(direction=(0, 1, 0), i_resolution=200, j_resolution=200).triangulate()


def colour_levels(scene):
    """Return the level in the colour ramp of each point's colour in a scene read back."""
    table = export_mod.ramp().astype(float)
    distance = ((scene['RGB'][:, None].astype(float) - table[None]) ** 2).sum(axis=-1)
    return np.argmin(distance, axis=1), len(table)


@pytest.mark.parametrize('kind', ['sphere', 'tube', 'plane'])
@pytest.mark.parametrize('association', ['point', 'cell'])
def test_a_very_large_surface_is_binned_near_the_budget_keeping_its_colours(
    tmp_path, monkeypatch, kind, association
):
    """A surface above the binning size lands near the budget, each point in its own colour."""
    monkeypatch.setattr(export_mod, 'BIN_POINTS', 1000)

    def decimated(*args, **kwargs):
        """Fail, as a surface above the binning size is never decimated."""
        pytest.fail('a surface above the binning size was decimated')

    monkeypatch.setattr(pv.PolyData, 'decimate_pro', decimated)
    full = binnable(kind)
    budget = 5000
    centres = full.points if association == 'point' else full.cell_centers().points
    getattr(full, f'{association}_data')['height'] = centres[:, 2]
    out = written(full, tmp_path, max_points=budget)
    assert 0.5 * budget <= out.n_points <= budget
    assert out.n_faces > 0
    levels, size = colour_levels(out)
    z = out.points[:, 2]
    off = np.abs(levels - (z - z.min()) / np.ptp(z) * (size - 1))
    if association == 'point':
        # A bin keeps one of its own points, with that point's value.
        assert off.max() <= 2
    else:
        # A face keeps the value of an input face it covers, at most a bin away.
        assert np.percentile(off, 99) <= 0.1 * size


def test_a_volume_like_surface_is_binned_under_the_budget(monkeypatch):
    """Nested shells, whose points grow faster than a sheet's, still end within the budget."""
    shells = pv.merge(
        [
            pv.Sphere(radius=r, theta_resolution=60, phi_resolution=60)
            for r in np.linspace(1, 3, 30)
        ]
    )
    out = export_mod.bin_to_budget(shells, 20_000)
    assert 0.5 * 20_000 <= export_mod.projected_points(out) <= 20_000


def test_a_binning_grid_stays_within_the_bin_limit():
    """However fine the bins asked for, the grid holds no more than the limit allows."""
    for sizes in ([1.0, 1.0, 1.0], [500.0, 1.0, 1.0], [1.0, 1.0, 0.0]):
        divisions = export_mod.bin_divisions(np.array(sizes), 1e-6)
        assert np.prod(divisions) <= export_mod.MAX_BINS
        assert (divisions >= 1).all()
        assert divisions[np.array(sizes) == 0].tolist() in ([], [1.0])


def test_a_surface_without_extent_or_area_is_not_binned_into_something():
    """Points in one place are left alone; faces along one line bin to nothing, not an error."""
    stacked = pv.PolyData(np.zeros((3, 3)), faces=[3, 0, 1, 2])
    assert export_mod.bin_to_budget(stacked, 1) is stacked
    line = pv.PolyData(
        np.c_[np.arange(30.0), np.zeros(30), np.zeros(30)],
        faces=np.c_[np.full(28, 3), np.arange(28), np.arange(1, 29), np.arange(2, 30)].ravel(),
    )
    assert export_mod.bin_to_budget(line, 5).n_faces == 0


def test_a_surface_with_no_area_left_is_empty(tmp_path, monkeypatch):
    """A surface whose faces all collapse when thinned is reported as empty, not written."""
    monkeypatch.setattr(export_mod, 'BIN_POINTS', 0)
    monkeypatch.setattr(export_mod, 'bin_to_budget', lambda surface, max_points: pv.PolyData())
    with pytest.raises(ValueError, match='no surface to show'):
        written(pv.Sphere(), tmp_path, max_points=10)


def test_a_scene_the_writer_cannot_write_is_an_error(tmp_path, monkeypatch):
    """A PLY writer that produces nothing is reported rather than left as a missing file."""

    class Silent:
        """A writer that accepts everything and writes nothing."""

        def __getattr__(self, name):
            return lambda *args: None

        def Write(self):  # noqa: N802
            return 0

    monkeypatch.setattr(export_mod._vtk, 'vtkPLYWriter', Silent)
    with pytest.raises(OSError, match='could not write'):
        export_mod.save_scene(pv.Sphere(), str(tmp_path / 'out.ply'))
