"""Tests for how the Quick Look info panel lays out what the describer reports."""

from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess
import sys
import warnings

from conftest import compiled
import numpy as np
import pytest
import pyvista as pv
from pyvista.examples import cells as cell_examples

from pyvista_quicklook import _dataset_info as info_mod

ROOT = Path(__file__).resolve().parent.parent
ELLIPSIS = info_mod.ELLIPSIS_TEXT
NBSP = info_mod.THOUSANDS
X = info_mod.TIMES
TRUNCATION = re.compile(rf'^{ELLIPSIS} \d[\d{NBSP}]* more [a-z ]+$')

pytestmark = pytest.mark.skipif(sys.platform != 'darwin', reason='AppKit is macOS only')


@pytest.fixture(scope='session')
def probe():
    """Compile the off-screen panel probe against the extension's own panel code."""
    sources = (
        ROOT / 'macos' / 'QuickLookExtension' / 'InfoPanel.swift',
        ROOT / 'macos' / 'Tools' / 'InfoProbe.swift',
    )
    return compiled('InfoProbe', sources, ('-O', '-parse-as-library', '-framework', 'AppKit'))


@pytest.fixture
def panel(probe, tmp_path):
    """Return a function laying out a dataset's panel and reporting its labels."""

    def laid_out(dataset, arrangement='automatic', size=(1400, 900), source='mesh.vtk'):
        described = info_mod.describe(dataset, source)
        info = tmp_path / 'info.json'
        info.write_text(json.dumps(described))
        finished = subprocess.run(
            [str(probe), str(info), str(size[0]), str(size[1]), arrangement],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        assert finished.returncode == 0, finished.stderr
        return {**json.loads(finished.stdout), 'described': described}

    return laid_out


def texts(report):
    """Return the text of every label shown, in layout order."""
    return [label['text'] for label in report['labels']]


def every_cell_type():
    """Return one grid holding one cell of every concrete cell type."""
    skipped = (pv.CellType.EMPTY_CELL, pv.CellType.CONVEX_POINT_SET)
    concrete = [t for t in pv.CellType if t not in skipped]
    with warnings.catch_warnings():
        warnings.simplefilter('ignore', pv.PyVistaDeprecationWarning)
        blocks = cell_examples.generate_cell_blocks(concrete, unsupported_action='squeeze')
    return blocks.combine()


def big_multiblock():
    """Return a nested composite with mixed, missing, and many blocks."""
    inner = pv.MultiBlock({'image': pv.ImageData(dimensions=(3, 3, 3)), 'gap': None})
    spheres = {f's{i}': pv.Sphere(theta_resolution=4, phi_resolution=4) for i in range(60)}
    return pv.MultiBlock({'inner': inner, **spheres})


def huge_user_dict():
    """Return a mesh whose user dict has far more keys than the panel shows."""
    mesh = pv.Sphere()
    mesh.user_dict['materials'] = {f'm{i}': {'density': i} for i in range(100)}
    mesh.user_dict.update({f'tag_{i:02d}': i for i in range(60)})
    return mesh


def many_arrays():
    """Return a mesh with more arrays than the panel lists."""
    mesh = pv.Sphere()
    for i in range(info_mod.MAX_ARRAYS + 5):
        mesh.point_data[f'a{i:02d}'] = np.full(mesh.n_points, float(i))
    return mesh


def long_names():
    """Return a composite whose names and text are longer than any column."""
    mesh = pv.Sphere()
    mesh.point_data['displacement_magnitude_of_a_very_long_array_name'] = np.zeros(mesh.n_points)
    mesh.field_data['solver'] = ['CalculiX version 2.21 with a long build description']
    mesh.user_dict['a_rather_long_key_name'] = 'and a value that runs on past the column'
    return pv.MultiBlock({'a_block_name_that_goes_on_and_on_for_a_while': mesh})


# Wide, narrow beside the render, wide below it, and narrow below it.
SIZES = [(1400, 900), (700, 500), (1000, 1000), (500, 900)]
DATASETS = [every_cell_type, big_multiblock, huge_user_dict, many_arrays, long_names]


@pytest.mark.parametrize('size', SIZES, ids=lambda size: f'{size[0]}x{size[1]}')
@pytest.mark.parametrize('dataset', DATASETS, ids=lambda f: f.__name__)
def test_every_label_fits_its_column(panel, dataset, size):
    """Every label fits its column, except a long name, which keeps its full text in a tooltip."""
    report = panel(dataset(), size=size)
    assert report['arrangement'] in ('side', 'below')
    assert report['labels']
    assert [label['text'] for label in report['labels'] if label['clipped']] == []
    assert [label['text'] for label in report['labels'] if label['overflows']] == []
    # Only names and user dict lines may be too long for a column; anything else was laid out
    # for the wrong width.
    info = report['described']
    allowed = {row['name'] for row in [*info.get('arrays', []), *info.get('blocks', [])]}
    allowed |= set(info.get('user_dict', []))
    shortened = [label for label in report['labels'] if label['shortened']]
    assert [label['text'] for label in shortened if label['tooltip'] not in allowed] == []


def test_long_names_are_shortened_in_a_narrow_column(panel):
    """A name too long for the column is shortened, and the tooltip keeps all of it."""
    labels = panel(long_names(), size=(700, 500))['labels']
    name = 'displacement_magnitude_of_a_very_long_array_name'
    [label] = [label for label in labels if label['tooltip'] == name]
    assert label['shortened']


@pytest.mark.parametrize(('width', 'table'), [(1000, True), (500, False)])
def test_arrays_below_are_a_table_only_when_it_fits(panel, width, table):
    """Below the render, arrays get a table when there is room and a list when there is not."""
    report = panel(long_names(), size=(width, 1000))
    assert report['arrangement'] == 'below'
    assert ('Assoc.' in texts(report)) is table


@pytest.mark.parametrize('size', [(1400, 900), (900, 1000)], ids=['side', 'below'])
@pytest.mark.parametrize(
    ('dataset', 'noun'),
    [
        (every_cell_type, 'cell types'),
        (big_multiblock, 'blocks'),
        (huge_user_dict, 'keys'),
        (many_arrays, 'arrays'),
    ],
    ids=['cell types', 'blocks', 'user dict', 'arrays'],
)
def test_truncation_reads_and_looks_the_same_everywhere(panel, dataset, noun, size):
    """Whatever is cut short ends in a dimmed '... N more <things>' line."""
    labels = panel(dataset(), size=size)['labels']
    marks = [label for label in labels if label['text'].lstrip().startswith(ELLIPSIS)]
    assert any(label['text'].lstrip().endswith(f'more {noun}') for label in marks)
    for label in marks:
        assert TRUNCATION.match(label['text'].strip()), label['text']
        assert label['color'] == 'secondary', label['text']


def test_cell_types_fill_four_lines_then_say_how_many_more(panel):
    """Cell types share lines, at most four, and the rest are counted."""
    grid = every_cell_type()
    shown = texts(panel(grid, 'side'))
    start = shown.index('Cell types') + 1
    end = next(i for i, text in enumerate(shown) if text.startswith(ELLIPSIS))
    lines = shown[start:end]
    assert len(lines) == 4
    assert all(line.count(', ') >= 1 for line in lines)
    named = sum(len(line.rstrip(',').split(', ')) for line in lines)
    total = len(grid.distinct_cell_types)
    assert shown[end] == f'{ELLIPSIS} {total - named} more cell types'


def test_a_short_list_of_cell_types_has_no_count(panel):
    """A single cell type has a singular label and no truncation line."""
    shown = texts(panel(pv.Sphere(), 'side'))
    assert shown[shown.index('Cell type') + 1] == 'Triangle'
    assert not any(text.startswith(ELLIPSIS) for text in shown)


def test_every_block_type_line_is_shown(panel):
    """Each leaf block type sits on its own line, alphabetically, None included."""
    shown = texts(panel(big_multiblock(), 'below'))
    start = shown.index('Block types') + 1
    assert shown[start : start + 3] == ['1 ImageData', '1 None', '60 PolyData']


def test_bounds_have_no_comma_or_brackets(panel):
    """Bounds are bare numbers in aligned columns."""
    shown = texts(panel(pv.Sphere(), 'side'))
    start = shown.index('Bounds') + 1
    bounds = shown[start : start + 15]
    assert bounds[0] == 'x'
    assert not any(mark in text for text in bounds for mark in ',[]')


@pytest.mark.parametrize(
    ('size', 'arrangement'),
    [
        ((400, 600), 'compact'),
        ((800, 300), 'compact'),
        ((1400, 900), 'side'),
        ((900, 1000), 'below'),
    ],
)
def test_the_arrangement_follows_the_panel_size(panel, size, arrangement):
    """Small panels get the summary, wide ones the side column, and tall ones the table below."""
    assert panel(pv.Sphere(), size=size)['arrangement'] == arrangement


@pytest.mark.parametrize('width', [180, 240, 300, 419])
def test_the_summary_never_splits_a_field(panel, width):
    """The compact summary wraps whole fields only, whatever the width."""
    report = panel(pv.Sphere(), size=(width, 300))
    assert report['arrangement'] == 'compact'
    [summary] = report['labels']
    assert not summary['clipped']
    lines = summary['text'].split('\n')
    assert (len(lines) > 1) == (width < 300)
    separator = f'{NBSP}· '
    fields = [part for line in lines for part in line.split(separator)]
    expected = ['PolyData', '842 points', f'1{NBSP}680 cells', '1 array']
    assert fields == [field.replace(' ', NBSP) for field in expected]


def test_the_summary_shows_image_dimensions_without_spaces(panel):
    """An image's summary gives its dimensions instead of point and cell counts."""
    grid = pv.ImageData(dimensions=(100, 100, 100))
    [summary] = panel(grid, size=(400, 300), source='volume.vti')['labels']
    assert summary['text'] == f'ImageData{NBSP}· 100{X}100{X}100'


def test_the_summary_counts_arrays_left_off_the_list(panel):
    """The summary gives the true number of arrays, not just the listed ones."""
    [summary] = panel(many_arrays(), size=(400, 300))['labels']
    assert summary['text'].endswith(f'{info_mod.MAX_ARRAYS + 6}{NBSP}arrays')


def test_a_dataset_with_nothing_to_show_is_empty(panel):
    """Something that is not a dataset has no facts, so the render is shown alone."""
    texture = pv.Texture(np.zeros((2, 2, 3), dtype=np.uint8))
    assert panel(texture, source='texture.unknown')['empty']
    assert not panel(pv.Sphere())['empty']


def test_headings_count_every_array_and_block(panel):
    """The Arrays and Blocks headings give the totals, including what the lists leave out."""
    shown = texts(panel(big_multiblock(), size=(1400, 900)))
    assert 'ARRAYS (1)' in shown
    assert 'BLOCKS (62)' in shown
    shown = texts(panel(many_arrays(), size=(1400, 900)))
    assert f'ARRAYS ({info_mod.MAX_ARRAYS + 6})' in shown
