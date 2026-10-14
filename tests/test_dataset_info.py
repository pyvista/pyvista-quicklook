"""Tests for the description shown in the info panel."""

from __future__ import annotations

import json

import numpy as np
import pytest
import pyvista as pv
import pyvista_zstd

from pyvista_quicklook import _dataset_info as info_mod
from pyvista_quicklook import _scene_export as export_mod
from pyvista_quicklook import convert

X = info_mod.TIMES


def described(dataset, tmp_path, name, **save):
    """Save a dataset, read it back, and describe it."""
    path = tmp_path / name
    if name.endswith('.pv'):
        pyvista_zstd.write(dataset, path)
    else:
        dataset.save(path, **save)
    return info_mod.describe(pv.read(path), str(path))


def facts(info, key='facts'):
    """Return a description's rows as a dictionary."""
    return dict(info[key])


def test_image_data_lists_its_grid(tmp_path):
    """An image reports dimensions, spacing, and origin, and no cell types."""
    image = pv.ImageData(dimensions=(4, 5, 6), spacing=(0.5, 1, 2), origin=(1, 2, 3))
    image['scalars'] = np.arange(image.n_points, dtype=np.float32)
    info = described(image, tmp_path, 'grid.vti')
    rows = facts(info)
    assert info['type'] == 'ImageData'
    assert rows['Points'] == '120'
    assert info_mod.count(1_234_567) == f'1{info_mod.THOUSANDS}234{info_mod.THOUSANDS}567'
    assert rows['Dimensions'] == f'4{X}5{X}6'
    assert rows['Spacing'] == '0.5, 1.0, 2.0'
    # Where the image lies is in its bounds; an origin alone misleads once it is offset.
    assert 'Origin' not in rows
    assert rows['Bounds'] == [['x', '1.0', '2.5'], ['y', '2.0', '6.0'], ['z', '3.0', '13.0']]
    assert rows['Cell type'] == ['Voxel']
    assert 'Direction' not in rows
    [array] = info['arrays']
    assert array['range'] == '[0.0, 119.0]'
    assert array['active'] == ['scalars']
    assert 'size' not in array


@pytest.mark.parametrize(
    ('name', 'expected'), [('flat.png', f'4{X}3'), ('flat.vti', f'4{X}3{X}1')]
)
def test_flat_images_drop_the_single_slice_only_for_raster_formats(tmp_path, name, expected):
    """A raster image shows two dimensions, a VTK image of one slice still shows three."""
    image = pv.ImageData(dimensions=(4, 3, 1))
    image['values'] = np.arange(12, dtype=np.uint8)
    assert facts(described(image, tmp_path, name))['Dimensions'] == expected


def test_a_rotated_image_says_so(tmp_path):
    """An image whose axes are turned is marked as such."""
    image = pv.ImageData(dimensions=(2, 2, 2), direction_matrix=np.diag([1, -1, -1]))
    assert facts(info_mod.describe(image, str(tmp_path / 'none')))['Direction'] == 'rotated'


def test_unstructured_grid_lists_cell_types_and_arrays(tmp_path):
    """Cell types are named, and point, cell, and field arrays each get a row."""
    grid = pv.ImageData(dimensions=(3, 3, 3)).cast_to_unstructured_grid()
    grid.point_data['displacement'] = np.ones((grid.n_points, 3))
    grid.cell_data['part'] = np.arange(grid.n_cells)
    grid.field_data['solver'] = ['CalculiX']
    info = described(grid, tmp_path, 'grid.vtu')
    assert facts(info)['Cell type'] == ['Voxel']
    rows = {row['name']: row for row in info['arrays']}
    assert rows['displacement']['components'] == 3
    assert rows['displacement']['range'] == '||v|| 1.732'
    assert rows['part']['association'] == 'Cell'
    assert rows['solver']['dtype'] == 'string'
    assert rows['solver']['range'] == 'CalculiX'


def test_structured_grid_lists_dimensions(tmp_path):
    """A structured grid reports its dimensions."""
    grid = pv.ImageData(dimensions=(2, 3, 4)).cast_to_structured_grid()
    assert facts(described(grid, tmp_path, 'grid.vts'))['Dimensions'] == f'2{X}3{X}4'


def test_every_cell_type_is_named():
    """Every distinct cell type is listed, however many; the panel decides how many fit."""
    cells = {
        kind: np.arange(n).reshape(1, -1)
        for kind, n in [
            (pv.CellType.VERTEX, 1),
            (pv.CellType.LINE, 2),
            (pv.CellType.TRIANGLE, 3),
            (pv.CellType.QUAD, 4),
            (pv.CellType.TETRA, 4),
            (pv.CellType.PYRAMID, 5),
            (pv.CellType.WEDGE, 6),
            (pv.CellType.HEXAHEDRON, 8),
        ]
    }
    grid = pv.UnstructuredGrid(cells, np.random.default_rng(0).random((8, 3)))
    assert len(facts(info_mod.describe(grid, 'none'))['Cell types']) == len(
        grid.distinct_cell_types
    )


def test_many_arrays_are_cut_short(tmp_path):
    """Past the limit, the remaining arrays are counted in one row."""
    mesh = pv.Sphere()
    for index in range(info_mod.MAX_ARRAYS + 5):
        mesh.point_data[f'a{index}'] = np.zeros(mesh.n_points)
    rows = info_mod.describe(mesh, 'none')['arrays']
    assert rows[-1] == {'name': '\u2026 6 more arrays', 'more': True}


def test_each_kind_of_array_reports_its_range_in_its_own_terms():
    """Ranges come from get_data_range, of the magnitude for vectors and modulus for complex."""
    mesh = pv.PolyData(np.zeros((2, 3)))
    mesh.point_data['floats'] = [0.0, 580.0]
    mesh.point_data['ints'] = [-3, 7]
    mesh.point_data['vectors'] = [[3.0, 4.0, 0.0], [-1.0, 0.0, 0.0]]
    mesh.point_data['mixed'] = [True, False]
    mesh.point_data['all_true'] = [True, True]
    mesh.point_data['nan'] = [np.nan, np.nan]
    mesh.point_data['complex'] = [3 + 4j, 0j]
    mesh.point_data['nan_vectors'] = np.full((2, 3), np.nan)
    mesh.point_data['masks'] = [[True, False, False], [True, True, True]]
    mesh.field_data['time'] = [0.25, 3.0]
    rows = {row['name']: row['range'] for row in info_mod.describe(mesh, 'none')['arrays']}
    assert rows['floats'] == '[0.0, 580.0]'
    assert rows['ints'] == '[-3, 7]'
    assert rows['vectors'] == '||v|| [1.0, 5.0]'
    assert rows['mixed'] == '[False, True]'
    assert rows['all_true'] == 'True'
    assert rows['nan'] == 'NaN'
    assert rows['complex'] == '||z|| [0.0, 5.0]'
    assert rows['nan_vectors'] == 'NaN'
    assert rows['masks'] == '||v|| [1.0, 1.732]'
    assert rows['time'] == '[0.25, 3.0]'
    assert info_mod.data_range(mesh, 'none', 'Point', np.array([])) is None
    assert (
        info_mod.range_text(info_mod.data_range(mesh, 'c', 'Point', np.array([np.nan * 1j])))
        == 'NaN'
    )
    assert info_mod.text_preview(np.array(['a', 'b', 'c', 'd'])) == 'a, b, c, \u2026'


def test_same_name_on_points_cells_and_field_is_three_rows(tmp_path):
    """One name on each association gives a row each, with its own range and one active row."""
    image = pv.ImageData(dimensions=(4, 4, 4))
    image.point_data['temperature'] = np.linspace(0, 1, image.n_points)
    image.cell_data['temperature'] = np.linspace(100, 200, image.n_cells)
    image.field_data['temperature'] = [42.0]
    rows = described(image, tmp_path, 'same_name.vti')['arrays']
    assert [(row['association'], row['range'], row['active']) for row in rows] == [
        ('Point', '[0.0, 1.0]', ['scalars']),
        ('Cell', '[100.0, 200.0]', []),
        ('Field', '42.0', []),
    ]


def test_odd_arrays_survive_a_file(tmp_path):
    """Complex, text, and boolean arrays read back from a file are all described."""
    mesh = pv.Sphere()
    mesh.point_data['complex'] = np.full(mesh.n_points, 3 + 4j)
    mesh.point_data['labels'] = np.array(['p'] * mesh.n_points)
    mesh.point_data['flags'] = np.ones(mesh.n_points, dtype=bool)
    rows = {row['name']: row for row in described(mesh, tmp_path, 'odd.vtp')['arrays']}
    assert rows['complex']['range'] == '||z|| 5.0'
    assert rows['labels']['dtype'] == 'string'
    assert rows['flags']['range'] == 'True'


def test_user_dict_gets_its_own_lines_and_no_array_row(tmp_path):
    """The user dict is shown as JSON lines, and its field array is not listed."""
    mesh = pv.Sphere()
    mesh.user_dict.update(
        {
            'name': 'mesh',
            'solver': {'name': 'CalculiX', 'options': {'steps': 40, 'tolerances': [1e-6, 1e-8]}},
            'history': [f'step {i}' for i in range(200)],
        }
    )
    info = described(mesh, tmp_path, 'tagged.vtp')
    assert info['user_dict'][0] == '"name": "mesh",'
    assert info['user_dict'][1] == '"solver": {'
    assert '"history": ["step 0", \u2026 200 items]' in info['user_dict']
    assert all(row['name'] != info_mod.USER_DICT_KEY for row in info['arrays'])


def test_a_dataset_without_a_user_dict_has_no_section():
    """No user dict, or one that is empty or not JSON, adds nothing."""
    assert 'user_dict' not in info_mod.describe(pv.Sphere(), 'none')
    mesh = pv.Sphere()
    mesh.field_data[info_mod.USER_DICT_KEY] = ['not json']
    assert info_mod.user_dict(mesh) == []
    mesh.field_data[info_mod.USER_DICT_KEY] = ['{}']
    assert info_mod.user_dict(mesh) == []


def test_user_dict_is_cut_short_everywhere():
    """Long strings, long lists, wide and deep dicts, and many lines are all cut short."""
    lines = info_mod.pretty(
        {
            'text': 'x' * 100,
            'wide': {f'k{i}': 'v' * 10 for i in range(20)},
            'deep': {'a': {'b': {'c': {'d': list(range(30)), 'e': 'f' * 30}}}},
            'pairs': {'k': [{'long': 'y' * 30} for _ in range(5)], 'x': 'z' * 40},
        },
        0,
        info_mod.MAX_DEPTH,
    )
    assert lines[0].endswith('\u2026",')
    assert any(line.strip().startswith('\u2026 13 more keys') for line in lines)
    assert any('keys}' in line for line in lines)
    assert any('items]' in line for line in lines)
    long = pv.Sphere()
    long.user_dict.update({f'key{i}': i for i in range(50)})
    assert info_mod.user_dict(long)[-1] == '\u2026 21 more keys'


def test_huge_user_dict_shows_every_top_level_key_it_can():
    """Top-level keys come before nested detail, and big dicts expand only as room allows."""
    mesh = pv.Sphere()
    mesh.user_dict['materials'] = {
        f'm{i:03d}': {'density': i, 'modulus': 2.1e11} for i in range(150)
    }
    mesh.user_dict.update({f'tag_{i:02d}': i for i in range(20)})
    lines = info_mod.user_dict(mesh)
    assert len(lines) <= info_mod.MAX_DICT_LINES
    assert lines[-1] == '"tag_19": 19'
    assert lines[0] == '"materials": {'
    assert lines[1] == '  "m000": {"density": 0, "modulus": 210000000000.0},'
    assert '  \u2026 143 more keys' in lines


def test_text_previews_are_cut_short():
    """An empty text array has no preview, and a long string is cut short."""
    assert info_mod.text_preview(np.array([], dtype=str)) == ''
    assert info_mod.text_preview(np.array(['x' * 50])).endswith('…')


def test_bounds_are_always_floats():
    """Bounds keep a decimal point, and use six significant figures."""
    assert info_mod.real(100) == '100.0'
    assert info_mod.real(-0.49926671385765076) == '-0.499267'
    assert info_mod.real(1.5e-12) == '1.5e-12'
    assert info_mod.real(float('nan')) == 'nan'


def test_numbers_are_formatted_for_a_narrow_column():
    """Numbers keep plain digits up to six figures, then an exponent without padding."""
    assert info_mod.digits(0, 4) == '0'
    assert info_mod.digits(0.123456, 4) == '0.1235'
    assert info_mod.digits(1.5e-7, 4) == '1.5e-7'
    assert info_mod.digits(2.5e12, 4) == '2.5e12'
    assert info_mod.digits(-10420.0, 4) == '-10420'
    assert info_mod.digits(1234567.0, 4) == '1.235e6'
    assert info_mod.real(-10420.0, 4) == '-10420.0'
    assert info_mod.size(512) == '512 bytes'
    assert info_mod.size(2_680_000) == '2.68 MB'
    assert info_mod.size(5e15) == '5000 TB'
    mesh = pv.PolyData(np.zeros((2, 3)))
    mesh.point_data['wide'] = [-10420.0, 6527.0]
    [row] = info_mod.describe(mesh, 'none')['arrays']
    assert row['range'] == '[-10420.0, 6527.0]'


def test_multiblock_lists_blocks_and_merges_arrays(tmp_path):
    """A composite reports its blocks and one row per array across them."""
    sphere = pv.Sphere()
    cube = pv.Cube()
    cube.point_data['Normals'] = np.zeros((cube.n_points, 3), dtype=np.float64)
    inner = pv.MultiBlock({'cube': cube, 'cone': pv.Cone()})
    nested = pv.MultiBlock({'sphere': sphere, 'inner': inner})
    info = described(nested, tmp_path, 'combo.vtm')
    rows = facts(info)
    assert rows['Blocks'] == '3'
    assert rows['Depth'] == '2'
    assert rows['Block type'] == '3 PolyData'
    assert [block['name'] for block in info['blocks']] == ['sphere', 'inner/cube', 'inner/cone']
    assert {block['dimensions'] for block in info['blocks']} == {''}
    [normals] = [row for row in info['arrays'] if row['name'] == 'Normals']
    assert normals['blocks'] == '2 of 3'
    assert normals['dtype'] == 'mixed'
    # Unit normals and zeroed ones, as magnitudes.
    assert normals['range'] == '||v|| [0.0, 1.0]'


def test_blocks_list_their_dimensions():
    """A flat and a solid image in one composite each report their own dimensions."""
    mixed = pv.MultiBlock(
        {'flat': pv.ImageData(dimensions=(4, 3, 1)), 'solid': pv.ImageData(dimensions=(2, 2, 2))}
    )
    blocks = info_mod.describe(mixed, 'mixed.vtm')['blocks']
    assert [block['dimensions'] for block in blocks] == [f'4{X}3{X}1', f'2{X}2{X}2']


def test_merged_ranges_span_every_block():
    """An array in several blocks gets one range across them, unless the kinds disagree."""
    first, second = pv.Sphere(), pv.Cube()
    first.point_data['t'] = np.linspace(0, 1, first.n_points)
    second.point_data['t'] = np.arange(second.n_points)
    first.point_data['z'] = np.full(first.n_points, 3 + 4j)
    second.point_data['z'] = np.zeros(second.n_points)
    first.point_data['s'] = np.array(['a'] * first.n_points)
    second.point_data['s'] = np.zeros(second.n_points)
    rows = {
        row['name']: row
        for row in info_mod.describe(pv.MultiBlock([first, second]), 'x')['arrays']
    }
    assert rows['t']['range'] == '[0.0, 7.0]'
    assert rows['z']['range'] == ''
    assert rows['s']['range'] == ''
    assert rows['s']['dtype'] == 'mixed'


def test_many_blocks_are_cut_short():
    """Past the limit, the remaining blocks are counted in one row."""
    blocks = pv.MultiBlock([pv.Sphere()] * (info_mod.MAX_BLOCKS + 3))
    assert info_mod.describe(blocks, 'none')['blocks'][-1]['name'] == '\u2026 3 more blocks'


def test_partitioned_dataset_is_described_as_blocks():
    """A partitioned dataset is described the way a composite is."""
    info = info_mod.describe(pv.PartitionedDataSet([pv.Sphere()]), 'none')
    assert info['type'] == 'PartitionedDataSet'
    assert facts(info)['Blocks'] == '1'


def test_something_that_is_not_a_dataset_has_no_facts():
    """Anything else read from a file is named and nothing more."""
    assert info_mod.describe(pv.Texture(np.zeros((2, 2, 3), np.uint8)), 'none')['facts'] == []


def test_zstd_container_reports_its_compression(tmp_path):
    """A pyvista-zstd file reports its version, compression, and ratio."""
    image = pv.ImageData(dimensions=(20, 20, 20))
    image['zeros'] = np.zeros(image.n_points)
    rows = facts(described(image, tmp_path, 'image.pv'), 'file')
    assert rows['Format'].startswith('pyvista-zstd v')
    assert rows['Compression'] == 'zstandard level 3'
    assert rows['Uncompressed'].endswith(f'{X} ratio)')


@pytest.mark.parametrize(
    ('save', 'expected'),
    [
        ({'binary': True}, {'Format': 'VTK XML ', 'Encoding': 'binary'}),
        ({'binary': False}, {'Format': 'VTK XML ', 'Encoding': 'ascii', 'Compression': None}),
    ],
)
def test_vtk_xml_reports_its_encoding(tmp_path, save, expected):
    """A VTK XML file reports its version and how its arrays are stored."""
    rows = facts(described(pv.Sphere(), tmp_path, 'sphere.vtp', **save), 'file')
    for key, value in expected.items():
        assert rows.get(key) is None if value is None else rows[key].startswith(value)


def test_vtk_xml_compressor_is_named(tmp_path):
    """The compressor named in a VTK XML header is reported without its class prefix."""
    path = tmp_path / 'sphere.vtp'
    path.write_text('<VTKFile type="PolyData" version="1.0" compressor="vtkZLibDataCompressor">')
    rows = dict(info_mod.file_facts(path))
    assert rows['Compression'] == 'zlib'
    assert 'Encoding' not in rows


def test_xml_without_a_header_reports_nothing(tmp_path):
    """A file named like VTK XML that is not one adds no file rows."""
    path = tmp_path / 'fake.vtu'
    path.write_text('<other/>')
    assert info_mod.file_facts(path) == []


def test_legacy_vtk_reports_its_version_and_title(tmp_path):
    """A legacy file reports its version, encoding, and any title worth showing."""
    path = tmp_path / 'titled.vtk'
    pv.Sphere().save(path)
    lines = path.read_bytes().split(b'\n', 2)
    path.write_bytes(b'\n'.join([lines[0], b'My sphere', lines[2]]))
    rows = facts(info_mod.describe(pv.read(path), str(path)), 'file')
    assert rows['Format'].startswith('VTK legacy ')
    assert rows['Encoding'] == 'binary'
    assert rows['Title'] == 'My sphere'
    untitled = tmp_path / 'untitled.vtk'
    pv.Sphere().save(untitled)
    assert 'Title' not in dict(info_mod.legacy_facts(untitled))


def test_ply_reports_encoding_and_comment(tmp_path):
    """A PLY header's format and first comment are reported."""
    path = tmp_path / 'sphere.ply'
    pv.Sphere().save(path)
    rows = dict(info_mod.file_facts(path))
    assert rows['Format'] == 'PLY binary little endian'
    assert rows['Comment'] == 'VTK generated PLY File'


@pytest.mark.parametrize(('binary', 'expected'), [(True, 'STL binary'), (False, 'STL text')])
def test_stl_reports_text_or_binary(tmp_path, binary, expected):
    """An STL file is identified as text or binary from its size."""
    path = tmp_path / 'sphere.stl'
    pv.Sphere().save(path, binary=binary)
    rows = dict(info_mod.file_facts(path))
    assert rows['Format'] == expected


def test_unknown_or_unreadable_files_report_nothing(tmp_path):
    """No file rows for an unclaimed suffix, a folder, or a reader that fails."""
    assert info_mod.file_facts(tmp_path / 'mesh.obj') == []
    assert info_mod.file_facts(tmp_path / 'missing.ply') == []
    broken = tmp_path / 'broken.pv'
    broken.write_bytes(b'nope')
    assert info_mod.file_facts(broken) == []


def test_exporter_writes_the_description(tmp_path):
    """The exporter writes the description beside the scene when asked."""
    source = tmp_path / 'sphere.vtp'
    pv.Sphere().save(source)
    info = tmp_path / 'info.json'
    export_mod.export(str(source), str(tmp_path / 'out.ply'), 0, str(info))
    assert json.loads(info.read_text())['type'] == 'PolyData'


def test_a_failed_description_leaves_the_preview(tmp_path, monkeypatch, capsys):
    """A description that fails is skipped, and the scene is still written."""

    def fail(dataset, source, header=None):
        message = 'no'
        raise ValueError(message)

    monkeypatch.setattr(export_mod._dataset_info, 'describe', fail)
    source = tmp_path / 'sphere.vtp'
    pv.Sphere().save(source)
    info = tmp_path / 'info.json'
    export_mod.export(str(source), str(tmp_path / 'out.ply'), 0, str(info))
    assert not info.exists()
    assert (tmp_path / 'out.ply').is_file()
    assert 'info skipped: ValueError: no' in capsys.readouterr().err


def test_multiblock_counts_every_block_kind(tmp_path):
    """Block types count the leaves the block count counts, None blocks included, by name."""
    inner = pv.MultiBlock({'image': pv.ImageData(dimensions=(2, 2, 2)), 'gap': None})
    outer = pv.MultiBlock(
        {'mesh': pv.Sphere(), 'inner': inner, 'deeper': pv.MultiBlock({'leaf': inner.copy()})}
    )
    info = info_mod.describe(outer, 'x')
    rows = facts(info)
    assert rows['Blocks'] == str(info['block_count']) == '5'
    assert rows['Depth'] == '3'
    assert rows['Block types'] == '2 ImageData\n2 None\n1 PolyData'
    gap = next(block for block in info['blocks'] if block['name'] == 'inner/gap')
    assert gap['type'] == 'None'
    assert gap['points'] == ''


def test_single_kind_labels_are_singular():
    """One block type or cell type gets a singular label, and a flat composite has no depth."""
    rows = facts(info_mod.describe(pv.MultiBlock([pv.Sphere(), pv.Cone()]), 'x'))
    assert rows['Block type'] == '2 PolyData'
    assert 'Depth' not in rows
    assert facts(info_mod.describe(pv.Sphere(), 'x'))['Cell type'] == ['Triangle']


def test_the_array_count_includes_arrays_left_off_the_list():
    """The total survives the cap on listed rows, for the summary to report."""
    mesh = pv.Sphere()
    for i in range(info_mod.MAX_ARRAYS + 6):
        mesh.point_data[f'a{i:02d}'] = np.zeros(mesh.n_points)
    info = info_mod.describe(mesh, 'x')
    assert info['array_count'] == info_mod.MAX_ARRAYS + 7
    assert len(info['arrays']) == info_mod.MAX_ARRAYS + 1
    assert (
        info_mod.describe(pv.MultiBlock([mesh, mesh]), 'x')['array_count']
        == info_mod.MAX_ARRAYS + 7
    )


def test_integer_ranges_are_whole_numbers_however_large():
    """An integer range keeps every digit rather than turning into a float."""
    mesh = pv.Sphere()
    mesh.point_data['id'] = np.array([-1] + [2**62] * (mesh.n_points - 1), dtype=np.int64)
    [row] = [row for row in info_mod.describe(mesh, 'x')['arrays'] if row['name'] == 'id']
    assert row['range'] == f'[-1, {2**62}]'


def test_long_user_dict_keys_are_cut_short():
    """A key is cut like any other string, so no line grows with it."""
    mesh = pv.Sphere()
    mesh.user_dict['k' * 500] = {'n' * 500: 1}
    lines = info_mod.user_dict(mesh)
    assert all(len(line) <= info_mod.MAX_LINE for line in lines)
    assert lines[0].startswith('"kkk')


def test_a_user_dict_nested_too_deeply_still_leaves_a_panel():
    """A user dict deeper than the interpreter can walk is summarised, not fatal."""
    mesh = pv.Sphere()
    mesh.field_data[info_mod.USER_DICT_KEY] = ['{"a": ' * 3000 + '1' + '}' * 3000]
    info = info_mod.describe(mesh, 'x')
    assert info['type'] == 'PolyData'
    assert info['user_dict'][0].startswith(info_mod.ELLIPSIS_TEXT)


def header_and_full(mesh, tmp_path, name, **save):
    """Return the header-only and full descriptions of a dataset saved to a file."""
    path = tmp_path / name
    if path.suffix == '.pv':
        pyvista_zstd.write(mesh, path)
    else:
        mesh.save(path, **save)
    return info_mod.header_info(str(path)), info_mod.describe(pv.read(path), str(path))


def rows_by_name(info):
    """Return array rows keyed by name and association, without the truncation row."""
    return {(r['name'], r['association']): r for r in info['arrays'] if not r.get('more')}


def mixed_arrays():
    """Return a grid with point, cell, and field arrays of several kinds."""
    grid = pv.ImageData(dimensions=(4, 5, 6), spacing=(0.5, 1, 2), origin=(1, 2, 3))
    grid.point_data['height'] = np.arange(grid.n_points, dtype=np.float32)
    grid.point_data['flow'] = np.ones((grid.n_points, 3)) * [3, 0, 4]
    grid.cell_data['part'] = np.arange(grid.n_cells, dtype=np.int64) % 7
    grid.cell_data['labels'] = np.array(['a'] * grid.n_cells)
    grid.field_data['step'] = [42]
    grid.point_data.active_vectors_name = 'flow'
    grid.set_active_scalars('height')
    return grid


@pytest.mark.parametrize(
    'name',
    ['grid.pv', 'grid.vti', 'grid_ascii.vti', 'grid.vtu', 'grid.vtp', 'grid.vts', 'grid.vtr'],
)
def test_a_header_agrees_with_reading_the_file(tmp_path, name):
    """What a header alone says matches the full description, ranges included."""
    mesh = mixed_arrays()
    suffix = name.split('.')[-1]
    if suffix == 'vtu':
        mesh = mesh.cast_to_unstructured_grid()
    elif suffix == 'vtp':
        mesh = mesh.extract_surface(
            algorithm='dataset_surface', pass_pointid=False, pass_cellid=False
        )
    elif suffix == 'vts':
        mesh = mesh.cast_to_structured_grid()
    elif suffix == 'vtr':
        mesh = mesh.cast_to_rectilinear_grid()
    save = {'binary': False} if 'ascii' in name else {}
    head, full = header_and_full(mesh, tmp_path, name.replace('_ascii', ''), **save)
    assert head['type'] == full['type']
    shared = {'Points', 'Cells', 'Dimensions', 'Spacing'}
    assert [f for f in head['facts'] if f[0] in shared] == [
        f for f in full['facts'] if f[0] in shared
    ]
    assert head['array_count'] == full['array_count']
    heads, fulls = rows_by_name(head), rows_by_name(full)
    assert set(heads) == set(fulls)
    for key, row in heads.items():
        for field in ('dtype', 'components', 'active'):
            assert row[field] == fulls[key][field], (key, field)
        if row['range']:
            assert row['range'] == fulls[key]['range'], key
    assert head['file'] == full['file']


def test_an_xml_header_gives_ranges_and_says_when_one_is_a_magnitude(tmp_path):
    """VTK XML records ranges, of the values for one component and of the magnitude for more."""
    head, _ = header_and_full(mixed_arrays(), tmp_path, 'grid.vti')
    rows = rows_by_name(head)
    assert rows[('height', 'Point')]['range'] == '[0.0, 119.0]'
    assert rows[('part', 'Cell')]['range'] == '[0, 6]'
    assert rows[('flow', 'Point')]['range'] == f'{info_mod.VECTOR_NORM} 5.0'


def test_a_pv_header_reads_each_array_alone_for_its_range(tmp_path):
    """A ``.pv`` file's ranges are read array by array, and match the full description."""
    path = tmp_path / 'grid.pv'
    pyvista_zstd.write(mixed_arrays(), path)
    header = info_mod.zstd_header(path)
    assert all(row['extent'] is None for row in header['rows'])
    info_mod.zstd_ranges(path, header, skip={('height', 'Point')})
    extents = {row['name']: row['extent'] for row in header['rows']}
    assert extents['height'] is None
    assert extents['part'][:2] == (0, 6)
    assert extents['flow'] == (5.0, 5.0, 'f', info_mod.VECTOR_NORM)
    assert extents['labels'] is None


def test_a_pv_header_carries_the_user_dict(tmp_path):
    """A ``.pv`` user dict is read from its own frame, without the rest of the file."""
    mesh = pv.Sphere()
    # Stored as PyVista keeps a user dict, which some pyvista-zstd releases cannot write.
    mesh.field_data[info_mod.USER_DICT_KEY] = np.array([json.dumps({'name': 'sphere'})])
    path = tmp_path / 'tagged.pv'
    pyvista_zstd.write(mesh, path)
    assert pv.read(path).user_dict == {'name': 'sphere'}
    info = info_mod.header_info(str(path))
    assert info['user_dict'] == ['"name": "sphere"']
    assert info['array_count'] == 1


def test_polydata_headers_count_every_kind_of_cell(tmp_path):
    """Vertices, lines, strips, and polygons all count as cells in a PolyData header."""
    mesh = pv.Sphere() + pv.Line() + pv.PolyData(np.zeros((1, 3)))
    head, full = header_and_full(mesh, tmp_path, 'mixed.vtp')
    assert dict(head['facts'])['Cells'] == dict(full['facts'])['Cells']


def test_files_without_a_header_reader_get_only_their_file_facts(tmp_path):
    """Other formats, composites, and unreadable files give only the file's own facts."""
    path = tmp_path / 'mesh.vtk'
    pv.Sphere().save(path)
    info = info_mod.header_info(str(path))
    assert info == {'type': '', 'file': info_mod.file_facts(path)}
    blocks = tmp_path / 'blocks.vtm'
    pv.MultiBlock([pv.Sphere()]).save(blocks)
    assert info_mod.header_info(str(blocks))['type'] == ''
    broken = tmp_path / 'broken.pv'
    broken.write_text('nope')
    assert info_mod.header_info(str(broken))['type'] == ''


def test_a_header_fills_in_the_arrays_a_selective_read_left_out(tmp_path):
    """Rows come in header order, those read in full replacing the header's."""
    path = tmp_path / 'grid.pv'
    pyvista_zstd.write(mixed_arrays(), path)
    header = info_mod.zstd_ranges(path, info_mod.zstd_header(path))
    with pyvista_zstd.Reader(path) as reader:
        reader.selected_point_arrays = {'height'}
        reader.selected_cell_arrays = set()
        partial = reader.read()
    merged = info_mod.describe(partial, str(path), header)
    full = info_mod.describe(pv.read(path), str(path))
    assert merged['arrays'] == full['arrays']
    assert merged['array_count'] == full['array_count']
    assert merged['facts'] == full['facts']


def test_selective_reads_load_only_the_colour(tmp_path, monkeypatch):
    """The exporter reads only the array it colours by, yet describes every array."""
    mesh = mixed_arrays().cast_to_unstructured_grid()
    for name in ('grid.pv', 'grid.vtu'):
        path = tmp_path / name
        if name.endswith('.pv'):
            pyvista_zstd.write(mesh, path)
        else:
            mesh.save(path)
            monkeypatch.setattr(export_mod, 'SELECTIVE_BYTES', 0)
        dataset, header = export_mod.read_for_preview(str(path))
        assert set(dataset.array_names) <= {'height', 'labels', 'step'}
        assert dataset.active_scalars_name == 'height'
        described = info_mod.describe(dataset, str(path), header)
        full = info_mod.describe(pv.read(path), str(path))
        assert described['array_count'] == full['array_count']
        assert described['arrays'] == full['arrays']


def test_xml_tags_past_the_header_window_mean_a_whole_read(tmp_path, monkeypatch):
    """Cell arrays written after inline point data are never left out of a selective read."""
    mesh = pv.ImageData(dimensions=(20, 20, 20)).cast_to_unstructured_grid()
    mesh.point_data['big'] = np.random.default_rng(0).random((mesh.n_points, 3))
    mesh.cell_data['cellval'] = np.arange(mesh.n_cells, dtype=np.float64)
    mesh.set_active_scalars('cellval', preference='cell')
    path = tmp_path / 'inline.vtu'
    mesh.save(path)
    assert b'<AppendedData' not in path.read_bytes()
    assert info_mod.xml_header(path)['type'] == 'UnstructuredGrid'
    monkeypatch.setattr(info_mod, 'XML_TAGS_HEAD', 4096)
    monkeypatch.setattr(export_mod, 'SELECTIVE_BYTES', 0)
    assert info_mod.xml_header(path) == {}
    dataset, header = export_mod.read_for_preview(str(path))
    assert header is None
    whole = pv.read(path)
    assert dataset.active_scalars_name == whole.active_scalars_name
    assert sorted(dataset.array_names) == sorted(whole.array_names)


def test_small_xml_files_are_read_whole(tmp_path):
    """An XML file below the selective size is read with every array, ranges exact."""
    path = tmp_path / 'grid.vtu'
    mixed_arrays().cast_to_unstructured_grid().save(path)
    dataset, header = export_mod.read_for_preview(str(path))
    assert header is None
    assert 'part' in dataset.array_names


@pytest.mark.parametrize(
    ('rows', 'expected'),
    [
        ([], None),
        ([{'name': 'a', 'association': 'Point', 'active': []}], None),
        ([{'name': 't', 'association': 'Cell', 'active': ['scalars']}], ('t', 'Cell')),
        (
            [
                {'name': 'vtkOriginalPointIds', 'association': 'Point', 'active': ['scalars']},
                {'name': 'vtkGhostType', 'association': 'Point', 'active': []},
                {'name': 'p', 'association': 'Cell', 'active': []},
            ],
            ('p', 'Cell'),
        ),
        ([{'name': 'vtkGhostType', 'association': 'Point', 'active': ['scalars']}], None),
        (
            [{'name': 'vtkIds', 'association': 'Point', 'active': ['scalars']}],
            ('vtkIds', 'Point'),
        ),
        (
            [
                {'name': 'vtkGhostType', 'association': 'Cell', 'active': ['scalars']},
                {'name': 'n', 'association': 'Point', 'active': ['normals']},
                {'name': 'region', 'association': 'Cell', 'active': []},
            ],
            ('region', 'Cell'),
        ),
    ],
)
def test_the_colour_is_chosen_from_the_header_as_from_the_data(rows, expected):
    """The header's active scalars colour the preview, stepping over VTK's own arrays."""
    assert export_mod.colour_choice(rows) == expected


def test_the_header_mode_reads_no_values(tmp_path, monkeypatch, capsys):
    """``--header`` writes what the header says without reading the dataset."""
    path = tmp_path / 'grid.pv'
    pyvista_zstd.write(mixed_arrays(), path)
    out = tmp_path / 'head.json'
    monkeypatch.setattr(export_mod, 'read', lambda source: pytest.fail('read'))
    monkeypatch.setattr(export_mod.sys, 'argv', ['x', str(path), '--header', str(out)])
    assert export_mod.main() == 0
    assert json.loads(out.read_text())['type'] == 'ImageData'


def test_composites_and_unknown_formats_are_read_whole(tmp_path):
    """A file whose header cannot stand in for its arrays is read in full."""
    path = tmp_path / 'blocks.pv'
    pyvista_zstd.write(pv.MultiBlock([pv.Sphere()]), path)
    dataset, header = export_mod.read_for_preview(str(path))
    assert header is None
    assert isinstance(dataset, pv.MultiBlock)


def test_an_xml_file_coloured_by_cells_keeps_that_array(tmp_path, monkeypatch):
    """When the cells carry the colour, the selective read keeps that cell array."""
    grid = mixed_arrays().cast_to_unstructured_grid()
    grid.point_data.active_scalars_name = None
    grid.set_active_scalars('part', preference='cell')
    path = tmp_path / 'cells.vtu'
    grid.save(path)
    monkeypatch.setattr(export_mod, 'SELECTIVE_BYTES', 0)
    dataset, header = export_mod.read_for_preview(str(path))
    # The colour, and the text arrays the header cannot preview.
    assert sorted(dataset.cell_data.keys()) == ['labels', 'part']
    assert dataset.point_data.keys() == []
    assert dataset.active_scalars_name == 'part'
    assert {row['name'] for row in header['rows']} >= {'height', 'flow', 'labels'}


def test_a_structured_pv_header_gives_counts_without_image_facts(tmp_path):
    """A structured grid's header has its counts, and no spacing or origin to give."""
    head, full = header_and_full(mixed_arrays().cast_to_structured_grid(), tmp_path, 'grid.pv')
    labels = [label for label, _ in head['facts']]
    assert labels == ['Points', 'Cells']
    assert head['type'] == full['type'] == 'StructuredGrid'


def test_the_exporter_needs_something_to_write(monkeypatch, tmp_path):
    """Run with neither a scene, a description, nor a header, the exporter says what it needs."""
    monkeypatch.setattr(export_mod.sys, 'argv', ['x', str(tmp_path / 'a.vtp')])
    with pytest.raises(SystemExit):
        export_mod.main()


def test_the_block_count_includes_blocks_left_off_the_list():
    """Every dataset and empty block is counted, however many the list shows."""
    blocks = pv.MultiBlock([pv.Sphere()] * (info_mod.MAX_BLOCKS + 3) + [None])
    info = info_mod.describe(blocks, 'x')
    assert info['block_count'] == info_mod.MAX_BLOCKS + 4
    assert len(info['blocks']) == info_mod.MAX_BLOCKS + 1


def test_the_service_knows_which_formats_have_a_header():
    """The service, which cannot import PyVista, lists the formats with a header reader."""
    assert set(convert.HEADER_SUFFIXES) == set(info_mod.HEADERS)


def test_an_empty_composite_has_only_its_block_count():
    """A MultiBlock with nothing in it has no block types, points, or bounds to show."""
    facts, blocks = info_mod.composite_facts(pv.MultiBlock(), [])
    assert facts == [['Blocks', '0'], ['Points', '0'], ['Cells', '0']]
    assert blocks == []
    gap = pv.MultiBlock([None])
    facts, _ = info_mod.composite_facts(gap, info_mod.leaves(gap))
    assert dict(facts)['Block type'] == '1 None'
    assert 'Bounds' not in dict(facts)


def test_text_arrays_in_several_blocks_give_no_preview():
    """Strings that differ from block to block are not previewed from just one of them."""
    first, second = pv.Sphere(), pv.Cube()
    first.field_data['label'] = ['first']
    second.field_data['label'] = ['second']
    [row] = [row for row in info_mod.merge_arrays([first, second]) if row['name'] == 'label']
    assert row['dtype'] == 'string'
    assert row['range'] == ''
    assert row['blocks'] == '2 of 2'


def test_values_extent_skips_what_has_no_order():
    """Text and empty arrays have no range; vectors have the range of their magnitude."""
    assert info_mod.values_extent(np.array([2, 7, 3])) == (2, 7, 'i', '')
    assert info_mod.values_extent(np.array(['a', 'b'])) is None
    assert info_mod.values_extent(np.zeros((0, 3))) is None
    assert info_mod.values_extent(np.array([[3.0, 4.0], [0.0, 1.0]]))[:2] == (1.0, 5.0)


def test_appended_xml_names_its_encoding(tmp_path):
    """Appended data is described by its own encoding, and its header gives the arrays."""
    from pyvista import _vtk

    mesh = mixed_arrays().cast_to_unstructured_grid()
    path = tmp_path / 'appended.vtu'
    writer = _vtk.vtkXMLUnstructuredGridWriter()
    writer.SetFileName(str(path))
    writer.SetInputData(mesh)
    writer.SetDataModeToAppended()
    writer.EncodeAppendedDataOff()
    writer.Write()
    assert dict(info_mod.xml_facts(path))['Encoding'] == 'appended raw'
    head = info_mod.xml_header(path)
    assert {row['name'] for row in head['rows']} >= {'height', 'part', 'labels'}


def test_ply_comments_are_shown_only_when_there_are_some(tmp_path):
    """A PLY header gives its first comment, and none when it has none or never ends."""
    plain = tmp_path / 'plain.ply'
    plain.write_text('ply\nformat ascii 1.0\nelement vertex 0\nend_header\n')
    assert [fact[0] for fact in info_mod.ply_facts(plain)] == ['Format']
    endless = tmp_path / 'endless.ply'
    endless.write_text('ply\nformat ascii 1.0\n' + 'comment more\n' * 300)
    assert info_mod.ply_facts(endless) == [['Format', 'PLY ascii'], ['Comment', 'more']]


def test_a_text_stl_without_a_name_has_no_header(tmp_path):
    """A text STL whose solid is unnamed shows no header line."""
    path = tmp_path / 'unnamed.stl'
    path.write_text('solid\nendsolid\n')
    assert info_mod.stl_facts(path) == [['Format', 'STL text']]


def test_cell_types_of_something_without_cells_are_none():
    """Whatever cannot report its cell types gives none rather than failing."""
    assert info_mod.cell_types(object()) is None
    assert info_mod.cell_types(pv.PolyData()) is None


def test_a_pv_array_without_a_frame_is_left_without_a_range(tmp_path, monkeypatch):
    """An array the metadata lists but the file does not hold gets no range or user dict."""
    mesh = mixed_arrays()
    mesh.field_data[info_mod.USER_DICT_KEY] = np.array([json.dumps({'a': 1})])
    path = tmp_path / 'grid.pv'
    pyvista_zstd.write(mesh, path)
    monkeypatch.setattr(info_mod, 'zstd_frame', lambda *args: 'no such frame')
    header = info_mod.zstd_ranges(path, info_mod.zstd_header(path))
    assert 'user_dict' not in header
    assert all(row['extent'] is None and row['range'] == '' for row in header['rows'])


def test_an_xml_header_without_its_grid_tag_counts_nothing(tmp_path):
    """A header naming a type it then never opens has no counts and no arrays."""
    path = tmp_path / 'empty.vtu'
    path.write_text('<VTKFile type="UnstructuredGrid"></VTKFile>')
    head = info_mod.xml_header(path)
    assert head['facts'] == [['Points', '0'], ['Cells', '0']]
    assert head['rows'] == []


def test_a_selective_read_without_a_colour_reads_no_arrays(tmp_path):
    """With no active scalars nothing is read for colour, and the header lists every array."""
    mesh = pv.Sphere()
    mesh.point_data.clear()
    mesh.point_data['a'] = np.arange(mesh.n_points)
    mesh.point_data.active_scalars_name = None
    path = tmp_path / 'plain.pv'
    pyvista_zstd.write(mesh, path)
    dataset, header = export_mod.read_for_preview(str(path))
    assert dataset.array_names == []
    assert info_mod.describe(dataset, str(path), header)['arrays'][0]['name'] == 'a'


def test_xml_without_its_root_tag_has_no_facts(tmp_path):
    """A file named as VTK XML that does not open with a VTKFile tag reports nothing."""
    path = tmp_path / 'notes.vtu'
    path.write_text('not xml at all')
    assert info_mod.xml_facts(path) == []


def test_an_empty_dataset_has_no_bounds_or_cell_types():
    """A dataset with no points or cells lists neither bounds nor cell types."""
    rows = facts(info_mod.describe(pv.PolyData(), 'x'))
    assert 'Bounds' not in rows
    assert 'Cell type' not in rows
    assert 'Cell types' not in rows


def test_a_header_that_cannot_be_read_says_why(tmp_path, capsys, monkeypatch):
    """A header reader that fails is reported, and only the file's own facts are shown."""
    path = tmp_path / 'grid.vtu'
    pv.Sphere().cast_to_unstructured_grid().save(path)

    def broken(path):
        message = 'NumberOfPoints'
        raise KeyError(message)

    monkeypatch.setitem(info_mod.HEADERS, '.vtu', broken)
    info = info_mod.header_info(str(path))
    assert info['type'] == ''
    assert 'header skipped: KeyError' in capsys.readouterr().err


def test_a_non_ascii_array_name_survives_the_header(tmp_path, monkeypatch, capsys):
    """A UTF-8 name is read from the header as written, and the selective read enables it."""
    mesh = pv.Sphere()
    mesh.point_data['température'] = np.arange(mesh.n_points, dtype=float)
    mesh.set_active_scalars('température')
    path = tmp_path / 'warm.vtp'
    mesh.save(path)
    names = {row['name'] for row in info_mod.header_info(str(path))['arrays']}
    assert 'température' in names
    monkeypatch.setattr(export_mod, 'SELECTIVE_BYTES', 0)
    dataset, header = export_mod.read_for_preview(str(path))
    assert header is not None
    assert dataset.active_scalars_name == 'température'
    assert 'selective read skipped' not in capsys.readouterr().err
    assert info_mod.xml_attributes('Name="a &amp; b"') == {'Name': 'a & b'}


def test_header_and_values_agree_on_arrays_without_a_range(tmp_path):
    """An all-NaN array shows NaN and an empty one nothing, from the header as from the file."""
    mesh = pv.Sphere()
    mesh.point_data['nan'] = np.full(mesh.n_points, np.nan)
    mesh.field_data['empty'] = np.zeros(0)
    head, full = header_and_full(mesh, tmp_path, 'gaps.vtp')
    heads, fulls = rows_by_name(head), rows_by_name(full)
    for key in (('nan', 'Point'), ('empty', 'Field')):
        assert heads[key]['range'] == fulls[key]['range'], key
    assert heads[('nan', 'Point')]['range'] == 'NaN'
    assert heads[('empty', 'Field')]['range'] == ''
    # An all-NaN vector array is one VTK writes but cannot read back; its header still says NaN.
    inverted = {'RangeMin': '3.1622776601683796e+149', 'RangeMax': 'nan'}
    extent = info_mod.xml_range(inverted, 'f', info_mod.VECTOR_NORM, mesh.n_points)
    assert info_mod.range_text(extent) == 'NaN'
    assert info_mod.range_text(info_mod.values_extent(np.full((4, 3), np.nan))) == 'NaN'


def test_an_integer_vector_magnitude_matches_the_header(tmp_path):
    """The magnitude range of an integer vector array is VTK's own, as the header records it."""
    mesh = pv.Sphere()
    vectors = np.zeros((mesh.n_points, 3), dtype=np.int32)
    vectors[:, 0] = 3
    vectors[:, 1] = 4
    vectors[0] = (0, 0, 0)
    mesh.point_data['steps'] = vectors
    head, full = header_and_full(mesh, tmp_path, 'steps.vtp')
    key = ('steps', 'Point')
    assert rows_by_name(head)[key]['range'] == rows_by_name(full)[key]['range']
    assert rows_by_name(full)[key]['range'] == f'{info_mod.VECTOR_NORM} [0.0, 5.0]'


def test_merged_integer_ranges_keep_every_digit():
    """Two blocks' integer ranges are merged as integers, however large."""
    first = (np.int64(2**62 + 1), np.int64(2**62 + 1), 'i', '')
    second = (np.int64(-1), np.int64(-1), 'i', '')
    assert info_mod.range_text(info_mod.merge_extents(first, second)) == f'[-1, {2**62 + 1}]'
    assert info_mod.merge_extents(first, (0.5, 1.5, 'f', ''))[2] == 'f'
    assert info_mod.merge_extents((True, True, 'b', ''), (3, 4, 'i', ''))[2] == 'i'


@pytest.mark.parametrize('dimensions', [(1, 1, 1), (2, 1, 1), (2, 2, 1), (3, 3, 3), (0, 0, 0)])
def test_header_counts_match_vtk_for_every_grid_shape(dimensions):
    """A header's cell count for a structured extent is the count VTK reports."""
    image = pv.ImageData(dimensions=dimensions)
    _, points, cells = info_mod.grid_counts(list(image.extent))
    assert (points, cells) == (image.n_points, image.n_cells)


def test_pyvista_bookkeeping_fields_are_skipped_by_every_path(tmp_path):
    """Arrays PyVista keeps for itself are listed neither from the header nor from the file."""
    mesh = pv.Sphere()
    mesh.point_data['flags'] = np.ones(mesh.n_points, dtype=bool)
    mesh.point_data['z'] = np.full(mesh.n_points, 3 + 4j)
    mesh.field_data[info_mod.USER_DICT_KEY] = np.array([json.dumps({'name': 'sphere'})])
    for name in ('odd.pv', 'odd.vtp'):
        head, full = header_and_full(mesh, tmp_path, name)
        for info in (head, full):
            assert not any(
                row['name'].startswith(info_mod.PYVISTA_PREFIX) for row in info['arrays']
            )
        assert head['array_count'] == full['array_count']
        assert set(rows_by_name(head)) == set(rows_by_name(full))
    heads, fulls = rows_by_name(head), rows_by_name(full)
    assert fulls[('flags', 'Point')]['dtype'] == 'bool'
    assert fulls[('z', 'Point')]['dtype'] == 'complex128'
    # VTK XML stores a bool as bytes and a complex as two floats, and the header says so.
    assert heads[('flags', 'Point')]['dtype'] == 'uint8'
    assert heads[('z', 'Point')]['components'] == 2
    pv_head, _ = header_and_full(mesh, tmp_path, 'again.pv')
    assert rows_by_name(pv_head)[('flags', 'Point')]['dtype'] == 'bool'
    assert rows_by_name(pv_head)[('z', 'Point')]['dtype'] == 'complex128'


def test_an_unknown_cell_type_is_listed_by_its_id():
    """A cell type VTK has no name for is listed by its id beside the named ones."""
    cells = np.array([3, 0, 1, 2, 3, 0, 1, 2])
    types = np.array([pv.CellType.TRIANGLE, 99], dtype=np.uint8)
    grid = pv.UnstructuredGrid(cells, types, np.random.default_rng(0).random((3, 3)))
    assert facts(info_mod.describe(grid, 'x'))['Cell types'] == ['Triangle', 'Type 99']


def test_the_header_suffixes_are_the_header_readers():
    """The suffix set the service copies is the set of formats with a header reader."""
    assert frozenset(info_mod.HEADERS) == info_mod.HEADER_SUFFIXES
    assert info_mod.XML_HEADER_SUFFIXES < info_mod.XML_SUFFIXES
