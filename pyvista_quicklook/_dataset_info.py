"""Describe a dataset for the info panel beside its preview, every value formatted."""

from __future__ import annotations

import html
import json
import math
from pathlib import Path
import re
import sys
from typing import TYPE_CHECKING
from typing import Any
from typing import NamedTuple
import warnings

import numpy as np
import pyvista as pv
from pyvista.core.utilities.arrays import convert_array

if TYPE_CHECKING:
    from collections.abc import Iterable
    from collections.abc import Sequence

# Rows past these are summarised in a final line rather than listed.
MAX_ARRAYS = 40
MAX_BLOCKS = 40
# Header bytes searched for tags; a file whose tags run on past them is read whole.
XML_TAGS_HEAD = 8 * 1024 * 1024
# The norm a range was taken over: a complex modulus, or the magnitude of each tuple.
COMPLEX_NORM = '||z||'
VECTOR_NORM = '||v||'

# Typographic separators, spelled as escapes so they cannot be mistaken for ASCII.
TIMES = '\u00d7'
THOUSANDS = '\u00a0'

# Where PyVista keeps a dataset's user dict, as one JSON string in its field data.
USER_DICT_KEY = '_PYVISTA_USER_DICT'
# How much of a user dict is shown: characters per line, per string, items per list, lines.
MAX_LINE = 60
MAX_TEXT = 40
MAX_ITEMS = 6
MAX_DICT_LINES = 30
MAX_DEPTH = 2
MAX_KEYS = 8
ELLIPSIS = object()
ELLIPSIS_TEXT = '\u2026'

# Each association's label, PyVista attribute, and VTK getter.
ASSOCIATIONS = (
    ('Point', 'point_data', 'GetPointData'),
    ('Cell', 'cell_data', 'GetCellData'),
    ('Field', 'field_data', 'GetFieldData'),
)
# The roles an array can be active for, and the name each attribute set records for it.
ROLES = (
    ('scalars', 'active_scalars_name'),
    ('vectors', 'active_vectors_name'),
    ('normals', 'active_normals_name'),
    ('texture', 'active_texture_coordinates_name'),
)
# Significant figures a number keeps as plain digits before it is written with an exponent.
PLAIN_DIGITS = 6


def count(value: int) -> str:
    """Return an integer with spaces between thousands that never break across lines."""
    return f'{value:,}'.replace(',', THOUSANDS)


def as_multiblock(dataset: Any) -> Any:
    """Return a partitioned dataset as the composite PyVista describes, anything else as is."""
    if isinstance(dataset, pv.PartitionedDataSet):
        return pv.MultiBlock(list(dataset))
    return dataset


def triple(values: Any) -> str:
    """Return numbers as a comma separated list of floats."""
    return ', '.join(real(v) for v in values)


def size(nbytes: float) -> str:
    """Return a byte count the way the Finder shows one."""
    for unit in ('bytes', 'KB', 'MB', 'GB'):
        if nbytes < 1000:
            return f'{nbytes:.0f} {unit}' if unit == 'bytes' else f'{digits(nbytes, 3)} {unit}'
        nbytes /= 1000
    return f'{digits(nbytes, 3)} TB'


def digits(value: float, significant: int) -> str:
    """Return a number rounded to some significant figures, plain up to six and ``e`` beyond."""
    rounded = float(f'{float(value):.{significant}g}')
    text = f'{rounded:.{PLAIN_DIGITS}g}'
    return text.replace('e+0', 'e').replace('e-0', 'e-').replace('e+', 'e')


def real(value: float, significant: int = PLAIN_DIGITS) -> str:
    """Return a number as a float to the given significant figures, always with a decimal point."""
    text = digits(value, significant)
    return text if any(c in text for c in '.ein') else f'{text}.0'


def bounds(values: Sequence[float]) -> list[list[str]]:
    """Return six bounds as one minimum and maximum per axis."""
    return [
        [axis, real(values[i]), real(values[i + 1])] for axis, i in (('x', 0), ('y', 2), ('z', 4))
    ]


def dimensions(values: Sequence[int]) -> str:
    """Return a grid's dimensions joined by multiplication signs."""
    return TIMES.join(str(d) for d in values)


def direction(matrix: Any) -> str:
    """Return a direction matrix as one line of numbers per row."""
    return '\n'.join(triple(row) for row in np.asarray(matrix))


def cell_type_name(cell_type: pv.CellType) -> str:
    """Return a cell type's name as words."""
    return cell_type.name.title().replace('_', ' ')


def cell_types(dataset: pv.DataSet) -> list[str]:
    """Return the names of the distinct cell types PyVista reports, in cell type order."""
    return [cell_type_name(t) for t in sorted(dataset.distinct_cell_types)]


def bound(value: Any, kind: str) -> str:
    """Return one end of a range, as a float for float arrays and as reported otherwise."""
    if kind == 'f':
        return real(value, 4)
    if isinstance(value, str):
        return value
    if kind == 'b':
        return str(bool(value))
    return str(int(value))


# An array's minimum, maximum, value kind, and the norm it was taken over, if any.
Extent = tuple[Any, Any, str, str]


def vtk_extent(array: Any, is_complex: bool) -> Extent:
    """Return the range a VTK array reports: of its component, or of each tuple's magnitude."""
    if is_complex:
        return (*array.GetRange(-1), 'f', COMPLEX_NORM)
    if array.GetNumberOfComponents() > 1:
        return (*array.GetRange(-1), 'f', VECTOR_NORM)
    low, high = getattr(array, 'GetValueRange', array.GetRange)(0)
    return low, high, 'f' if isinstance(low, float) else 'i', ''


def components(shape: Sequence[int]) -> int:
    """Return how many components each tuple of an array of the given shape has."""
    return 1 if len(shape) < 2 else int(np.prod(shape[1:]))


def values_extent(values: np.ndarray) -> Extent | None:
    """Return the range VTK reports for an array's values, handed to VTK as PyVista stores them."""
    if values.dtype.kind not in 'biufc':
        return None
    tuples = np.ascontiguousarray(values)
    width = components(values.shape)
    if values.dtype.kind == 'c':
        # PyVista stores a complex value as its real and imaginary parts.
        tuples, width = tuples.view(values.real.dtype), 2 * width
    elif values.dtype.kind == 'b':
        # PyVista stores a bool as a byte.
        tuples = tuples.view(np.uint8)
    array = convert_array(tuples.reshape(values.shape[0] if values.ndim else 1, width))
    return vtk_extent(array, values.dtype.kind == 'c')


def reported_kind(low: Any, high: Any) -> str:
    """Return the value kind of a range's two ends as PyVista reports them."""
    if isinstance(low, np.bool_ | bool) and isinstance(high, np.bool_ | bool):
        return 'b'
    if all(isinstance(v, np.integer | int) and not isinstance(v, bool) for v in (low, high)):
        return 'i'
    return 'f'


def composite_extent(multiblock: pv.MultiBlock, name: str, association: str) -> Extent:
    """Return the range of a one component array across a composite, as PyVista reports it."""
    with warnings.catch_warnings():
        # A block of only NaN has no range of its own, which PyVista leaves out.
        warnings.simplefilter('ignore', RuntimeWarning)
        low, high = multiblock.get_data_range(
            name, preference=association.lower(), allow_missing=True
        )
    return low, high, reported_kind(low, high), ''


def range_text(extent: Extent | None) -> str:
    """Return a range as text: one value when both ends are equal, else an interval."""
    if extent is None:
        return ''
    low, high, kind, norm = extent
    if float(low) > float(high):
        # The inverted range VTK reports for an array without values.
        return ''
    if kind == 'f' and math.isnan(float(low)) and math.isnan(float(high)):
        text = 'NaN'
    else:
        text = bound(low, kind) if low == high else f'[{bound(low, kind)}, {bound(high, kind)}]'
    return f'{norm} {text}' if norm else text


def active_roles(data: Any, name: str, association: str) -> list[str]:
    """Return the roles an array is active for, from its own association's active names."""
    if association == 'Field':
        return []
    return [role for role, attribute in ROLES if getattr(data, attribute) == name]


def array_rows(dataset: pv.DataSet) -> list[dict[str, Any]]:
    """Return one row per array, point arrays first, then cell and field arrays."""
    rows = []
    for association, attribute, getter in ASSOCIATIONS:
        data = getattr(dataset, attribute)
        stored = getattr(dataset, getter)()
        for name in data:
            if name == USER_DICT_KEY:
                continue
            array = np.asarray(data[name])
            is_text = array.dtype.kind in 'OUS'
            vtk_array = None if is_text else stored.GetArray(name)
            rows.append(
                {
                    'name': name,
                    'association': association,
                    'dtype': 'string' if is_text else str(array.dtype),
                    'components': components(array.shape),
                    'range': text_preview(array) if is_text else '',
                    'extent': None
                    if vtk_array is None
                    else vtk_extent(vtk_array, array.dtype.kind == 'c'),
                    'active': active_roles(data, name, association),
                }
            )
    return rows


def text_preview(array: np.ndarray) -> str:
    """Return the first few strings of a text array, cut to fit a column."""
    values = [str(v) for v in array.ravel()[:4]]
    text = ', '.join(values[:3]) + (f', {ELLIPSIS_TEXT}' if array.size > 3 else '')
    return text if len(text) <= MAX_TEXT else text[: MAX_TEXT - 1] + ELLIPSIS_TEXT


def key_text(key: Any) -> str:
    """Return a dict key as a JSON string, cut short like any other string."""
    return inline(str(key), MAX_TEXT)


def inline(value: Any, limit: int) -> str:
    """Return a JSON value on one line, with long strings and lists cut short."""
    if isinstance(value, str):
        text = json.dumps(value, ensure_ascii=False)
        return text if len(text) <= MAX_TEXT else text[: MAX_TEXT - 2] + ELLIPSIS_TEXT + '"'
    if isinstance(value, list):
        items = value
        if len(value) > MAX_ITEMS:
            items = [*value[: MAX_ITEMS - 2], ELLIPSIS, value[-1]]
        parts = [ELLIPSIS_TEXT if v is ELLIPSIS else inline(v, limit) for v in items]
        text = '[' + ', '.join(parts) + ']'
    elif isinstance(value, dict):
        text = (
            '{' + ', '.join(f'{key_text(k)}: {inline(v, limit)}' for k, v in value.items()) + '}'
        )
    else:
        return json.dumps(value)
    if len(text) <= limit:
        return text
    if isinstance(value, list):
        short = f'[{inline(value[0], limit)}, {ELLIPSIS_TEXT} {len(value)} items]'
        return short if len(short) <= limit else f'[{ELLIPSIS_TEXT} {len(value)} items]'
    return f'{{{ELLIPSIS_TEXT} {len(value)} keys}}'


def entry(key: Any, item: Any, indent: int, comma: str, depth: int) -> list[str]:
    """Return the lines for one key, expanding a dict at most ``depth`` levels down."""
    pad = '  ' * indent
    head = f'{pad}{key_text(key)}: '
    whole = inline(item, sys.maxsize)
    if len(head) + len(whole) <= MAX_LINE:
        return [head + whole + comma]
    if not isinstance(item, dict) or depth <= 0:
        return [head + inline(item, MAX_LINE - len(head)) + comma]
    return [head + '{', *pretty(item, indent + 1, depth - 1), pad + '}' + comma]


def pretty(value: dict[Any, Any], indent: int, depth: int) -> list[str]:
    """Return a nested dict as lines of JSON, showing at most a few of its keys."""
    shown = list(value.items())
    if len(shown) > MAX_KEYS:
        shown = shown[: MAX_KEYS - 1]
    last = len(value) - 1
    lines = []
    for position, (key, item) in enumerate(shown):
        lines += entry(key, item, indent, '' if position == last else ',', depth)
    if len(shown) < len(value):
        lines.append(f'{"  " * indent}{ELLIPSIS_TEXT} {len(value) - len(shown)} more keys')
    return lines


def user_dict(dataset: Any) -> list[str]:
    """Return the lines showing the user dict PyVista reads for a dataset, or none without one."""
    if USER_DICT_KEY not in dataset.field_data:
        return []
    try:
        value = dict(dataset.user_dict)
    except ValueError:
        return []
    return user_dict_value_lines(value)


def user_dict_lines(stored: np.ndarray) -> list[str]:
    """Return the lines showing a user dict from the field array PyVista keeps it in."""
    try:
        value = json.loads(str(stored.ravel()[0]))
    except ValueError, IndexError:
        return []
    if not isinstance(value, dict):
        return []
    return user_dict_value_lines(value)


def user_dict_value_lines(value: dict[Any, Any]) -> list[str]:
    """Return the lines showing a user dict, or none when it is empty."""
    if not value:
        return []
    try:
        return budgeted(value)
    except RecursionError:
        return [f'{ELLIPSIS_TEXT} {len(value)} keys, nested too deeply to show']


def budgeted(value: dict[Any, Any]) -> list[str]:
    """Return a user dict as lines of JSON, every top-level key first, nesting as room allows."""
    items = list(value.items())
    hidden = len(items) - MAX_DICT_LINES + 1 if len(items) > MAX_DICT_LINES else 0
    shown = items[: len(items) - hidden]
    last = len(items) - 1
    commas = ['' if position == last else ',' for position in range(len(shown))]
    # Every shown key gets a line first, then nested dicts expand while lines remain.
    entries = [
        entry(key, item, 0, comma, 0) for (key, item), comma in zip(shown, commas, strict=True)
    ]
    spare = MAX_DICT_LINES - len(entries) - bool(hidden)
    for index, ((key, item), comma) in enumerate(zip(shown, commas, strict=True)):
        if not isinstance(item, dict):
            continue
        for depth in range(MAX_DEPTH, 0, -1):
            expanded = entry(key, item, 0, comma, depth)
            if len(expanded) - len(entries[index]) <= spare:
                spare -= len(expanded) - len(entries[index])
                entries[index] = expanded
                break
    lines = [line for lines in entries for line in lines]
    if hidden:
        lines.append(f'{ELLIPSIS_TEXT} {hidden} more keys')
    return lines


def merge_arrays(composite: Any, blocks: list[pv.DataSet]) -> list[dict[str, Any]]:
    """Return array rows for a composite dataset, one per name and association."""
    rows = merge_rows([array_rows(block) for block in blocks])
    if isinstance(composite, pv.MultiBlock):
        for row in rows:
            kinds = row['dtype'].split(', ')
            if row['components'] == 1 and not any(
                k.startswith(('string', 'complex')) for k in kinds
            ):
                row['extent'] = composite_extent(composite, row['name'], row['association'])
    return rows


def distinct(values: Iterable[Any]) -> list[Any]:
    """Return the distinct values in the order they first appear."""
    return list(dict.fromkeys(values))


def agreed(values: Iterable[Any]) -> Any:
    """Return the value every block agrees on, else the distinct values joined."""
    found = distinct(values)
    return found[0] if len(found) == 1 else ', '.join(str(v) for v in found)


def merge_rows(per_block: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Return one row per name and association from each block's array rows, without ranges."""
    merged: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for block_rows in per_block:
        for row in block_rows:
            merged.setdefault((row['name'], row['association']), []).append(row)
    rows = []
    for found in merged.values():
        roles = distinct(tuple(row['active']) for row in found)
        rows.append(
            {
                **found[0],
                'dtype': ', '.join(distinct(row['dtype'] for row in found)),
                'components': agreed(row['components'] for row in found),
                # Strings that differ from block to block are not previewed from just one.
                'range': found[0]['range'] if len(found) == 1 else '',
                'extent': None,
                'active': list(roles[0])
                if len(roles) == 1
                else ' / '.join(', '.join(r) or 'none' for r in roles),
                'blocks': f'{len(found)} of {len(per_block)}',
            }
        )
    return rows


def format_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Format each range, and cap how many array rows are listed."""
    for row in rows:
        extent = row.pop('extent')
        if row['dtype'] != 'string':
            row['range'] = range_text(extent)
    if len(rows) > MAX_ARRAYS:
        hidden = len(rows) - MAX_ARRAYS
        rows = rows[:MAX_ARRAYS]
        rows.append({'name': f'{ELLIPSIS_TEXT} {hidden} more arrays', 'more': True})
    return rows


def dataset_facts(dataset: pv.DataSet) -> list[list[Any]]:
    """Return the label and value rows that describe one dataset."""
    facts = [
        ['Points', count(dataset.n_points)],
        ['Cells', count(dataset.n_cells)],
    ]
    if hasattr(dataset, 'dimensions'):
        facts.append(['Dimensions', dimensions(dataset.dimensions)])
    if isinstance(dataset, pv.ImageData):
        facts.append(['Spacing', triple(dataset.spacing)])
        facts.append(['Direction', direction(dataset.direction_matrix)])
    types = cell_types(dataset)
    facts.append(['Cell type' if len(types) == 1 else 'Cell types', types])
    facts.append(['Bounds', bounds(dataset.bounds)])
    return facts


def leaves(multiblock: pv.MultiBlock) -> list[tuple[str, pv.DataSet | None]]:
    """Return every dataset and None block in a composite, named by its path of stored names."""
    return list(
        zip(
            multiblock.recursive_iterator('names', prepend_names=True, separator='/'),
            multiblock.recursive_iterator('blocks', skip_none=False),
            strict=True,
        )
    )


def partitions(partitioned: pv.PartitionedDataSet) -> list[tuple[str, pv.DataSet | None]]:
    """Return every partition of a partitioned dataset, named by its index."""
    return [(str(index), partition) for index, partition in enumerate(partitioned)]


def block_kind(block: Any) -> str:
    """Return the type name of a block, an empty one named None."""
    return type(block).__name__ if block is not None else 'None'


def composite_facts(
    composite: pv.MultiBlock | pv.PartitionedDataSet, found: list[tuple[str, pv.DataSet | None]]
) -> tuple[list[list[Any]], list[dict[str, Any]]]:
    """Return the summary rows and per-block rows of a composite, given its leaves."""
    entries = [
        BlockEntry(
            name,
            block_kind(block),
            block.n_points if block is not None else None,
            block.n_cells if block is not None else None,
            dimensions(block.dimensions) if hasattr(block, 'dimensions') else '',
        )
        for name, block in found
    ]
    if isinstance(composite, pv.PartitionedDataSet):
        n_blocks = composite.n_partitions
        extent = [0.0] * 6
        composite.GetBounds(extent)
    else:
        n_blocks, extent = composite.n_blocks, composite.bounds
    facts, blocks = composite_summary(
        n_blocks, composite.GetNumberOfPoints(), composite.GetNumberOfCells(), entries
    )
    facts.append(['Bounds', bounds(extent)])
    return facts, blocks


class BlockEntry(NamedTuple):
    """One dataset or empty block of a composite, as its block list shows it."""

    name: str
    kind: str
    points: int | None
    cells: int | None
    dimensions: str


def composite_summary(
    n_blocks: int, points: int | None, cells: int | None, found: list[BlockEntry]
) -> tuple[list[list[Any]], list[dict[str, Any]]]:
    """Return a composite's summary rows, bounds aside, and its per-block rows."""
    facts = [['Blocks', count(n_blocks)]]
    if points is not None:
        facts.append(['Points', count(points)])
    if cells is not None:
        facts.append(['Cells', count(cells)])
    kinds = sorted(distinct(entry.kind for entry in found), key=str.lower)
    facts.append(['Block type' if len(kinds) == 1 else 'Block types', '\n'.join(kinds)])
    blocks = [
        {
            'name': entry.name,
            'type': entry.kind,
            'points': count(entry.points) if entry.points is not None else '',
            'cells': count(entry.cells) if entry.cells is not None else '',
            'dimensions': entry.dimensions,
        }
        for entry in found[:MAX_BLOCKS]
    ]
    if len(found) > MAX_BLOCKS:
        hidden = len(found) - MAX_BLOCKS
        blocks.append({'name': f'{ELLIPSIS_TEXT} {hidden} more blocks', 'more': True})
    return facts, blocks


def zstd_facts(path: Path) -> list[list[str]]:
    """Return the container version, compression, and stored sizes of a ``.pv`` file."""
    import pyvista_zstd

    with pyvista_zstd.Reader(path) as reader:
        metadata = reader.file_metadata
        expanded = reader.nbytes
        compressed = getattr(reader, 'compressed_nbytes', None)
    facts = [
        ['Format', f'pyvista-zstd v{metadata.file_version}'],
        ['Compression', f'{metadata.compression} level {metadata.compression_level}'],
        ['Uncompressed', size(expanded)],
    ]
    if compressed is not None:
        facts.append(['Compressed', size(compressed)])
    return facts


def xml_facts(path: Path) -> list[list[str]]:
    """Return the version, compressor, and data encoding of a VTK XML file."""
    head = xml_text(path, XML_TAGS_HEAD)
    match = re.search(r'<VTKFile\b([^>]*)>', head)
    if match is None:
        return []
    attributes = xml_attributes(match.group(1))
    facts = [['Format', f'VTK XML {attributes.get("version", "")}'.strip()]]
    tags = head.split('<AppendedData', 1)[0]
    encodings = sorted(set(re.findall(r'format="(\w+)"', tags)))
    if 'appended' in encodings:
        appended = re.search(r'<AppendedData\b[^>]*encoding="(\w+)"', head)
        encodings[encodings.index('appended')] = (
            f'appended {appended.group(1)}' if appended else 'appended'
        )
    if encodings:
        facts.append(['Encoding', ', '.join(encodings)])
    if compressor := attributes.get('compressor'):
        name = compressor.removeprefix('vtk').removesuffix('DataCompressor')
        facts.append(['Compression', COMPRESSORS.get(name, name)])
    return facts


def legacy_facts(path: Path) -> list[list[str]]:
    """Return the version, encoding, and title of a legacy VTK file."""
    with path.open('rb') as handle:
        lines = [handle.readline(256).decode('latin-1').strip() for _ in range(3)]
    version = lines[0].removeprefix('# vtk DataFile Version').strip()
    return [
        ['Format', f'VTK legacy {version}'.strip()],
        ['Encoding', lines[2].lower()],
        ['Title', lines[1]],
    ]


def ply_facts(path: Path) -> list[list[str]]:
    """Return the encoding, comments, and object info in a PLY header."""
    facts = []
    found: dict[str, list[str]] = {'comment': [], 'obj_info': []}
    with path.open('rb') as handle:
        for _ in range(200):
            line = handle.readline(1024).decode('latin-1').strip()
            keyword = line.split(' ', 1)[0]
            if keyword == 'format':
                encoding = line.split()[1].replace('_', ' ')
                facts.append(['Format', f'PLY {encoding}'])
            elif keyword in found:
                found[keyword].append(line.split(' ', 1)[1] if ' ' in line else '')
            elif line == 'end_header' or not line:
                break
    for label, keyword in (('Comment', 'comment'), ('Object info', 'obj_info')):
        if found[keyword]:
            facts.append([label, '\n'.join(found[keyword])])
    return facts


def stl_facts(path: Path) -> list[list[str]]:
    """Return whether an STL file is text or binary, and its header text."""
    with path.open('rb') as handle:
        head = handle.read(84)
    triangles = int.from_bytes(head[80:84], 'little') if len(head) == 84 else -1
    is_binary = triangles >= 0 and path.stat().st_size == 84 + 50 * triangles
    facts = [['Format', 'STL binary' if is_binary else 'STL text']]
    header = head[:80].split(b'\0', 1)[0].decode('latin-1').strip()
    if not is_binary:
        header = header.removeprefix('solid').split('\n', 1)[0].strip()
    if header:
        facts.append(['Header', header[:80]])
    return facts


COMPRESSORS = {'ZLib': 'zlib', 'ZStd': 'zstandard'}

FILE_FACTS = {
    '.pv': zstd_facts,
    '.vtk': legacy_facts,
    '.ply': ply_facts,
    '.stl': stl_facts,
}
# Serial VTK XML files, whose header lists every array of the one dataset they hold.
XML_HEADER_SUFFIXES = frozenset({'.vtu', '.vtp', '.vti', '.vtr', '.vts'})
XML_SUFFIXES = XML_HEADER_SUFFIXES | {'.vtm', '.vthb', '.vtpd', '.vtpc', '.pvtu', '.pvtp'}


def xml_text(path: Path, limit: int) -> str:
    """Return the opening bytes of a VTK XML file as the UTF-8 text VTK writes."""
    with path.open('rb') as handle:
        return handle.read(limit).decode('utf-8', errors='replace')


def file_facts(path: Path) -> list[list[str]]:
    """Return what the file's own header says about how it is stored, if anything."""
    suffix = path.suffix.lower()
    reader = xml_facts if suffix in XML_SUFFIXES else FILE_FACTS.get(suffix)
    if reader is None or not path.is_file():
        return []
    try:
        return reader(path)
    except Exception:  # noqa: BLE001
        return []


# What a file header says about a dataset, from the format's own reader, without its values.
DATASET_TYPES = ('ImageData', 'RectilinearGrid', 'StructuredGrid', 'PolyData', 'UnstructuredGrid')
XML_DTYPES = {
    **{f'{k}{n}': f'{k.lower()}{n}' for k in ('Int', 'UInt') for n in (8, 16, 32, 64)},
    'Float32': 'float32',
    'Float64': 'float64',
    'String': 'string',
    'Bit': 'bool',
}
XML_SECTIONS = {'PointData': 'Point', 'CellData': 'Cell', 'FieldData': 'Field'}
# The counts a VTK XML piece records, by the dataset type it belongs to, and their labels.
XML_PIECE_COUNTS = {
    'UnstructuredGrid': (('NumberOfPoints', 'Points'), ('NumberOfCells', 'Cells')),
    'PolyData': (
        ('NumberOfPoints', 'Points'),
        ('NumberOfVerts', 'Verts'),
        ('NumberOfLines', 'Lines'),
        ('NumberOfStrips', 'Strips'),
        ('NumberOfPolys', 'Polys'),
    ),
}


def header_row(
    name: str, association: str, dtype: str, components: int, roles: list[str]
) -> dict[str, Any]:
    """Return an array row known only from a header, its range still to be found."""
    return {
        'name': name,
        'association': association,
        'dtype': dtype,
        'components': components,
        'range': '',
        'extent': None,
        'active': roles,
    }


def stored_roles(actives: dict[str, str | None], association: str, name: str) -> list[str]:
    """Return an array's roles from the active names a header records for its association."""
    return [role for role, _ in ROLES if actives.get(f'{association} {role}') == name]


def zstd_metadata(path: Path) -> Any:
    """Return the root dataset metadata of a ``.pv`` file, read without any of its arrays."""
    import pyvista_zstd

    with pyvista_zstd.Reader(path) as reader:
        return reader.dataset_metadata


def zstd_array(reader: Any, name: str, association: str) -> np.ndarray | None:
    """Return one array of a ``.pv`` dataset, or None when the file does not hold it."""
    try:
        return np.asarray(reader.read_array(name, association=association.lower()))
    except KeyError, RuntimeError:
        # RuntimeError: a damaged file lists the array but does not store it.
        return None


def zstd_dataset_facts(meta: Any, kind: str) -> list[list[Any]]:
    """Return the counts and grid a ``.pv`` dataset's metadata stores."""
    facts: list[list[Any]] = [['Points', count(meta.n_points)], ['Cells', count(meta.n_cells)]]
    if getattr(meta, 'dimensions', None) is not None:
        facts.append(['Dimensions', dimensions(meta.dimensions)])
    if kind == 'ImageData':
        if getattr(meta, 'spacing', None) is not None:
            facts.append(['Spacing', triple(meta.spacing)])
        if getattr(meta, 'direction_matrix', None) is not None:
            facts.append(['Direction', direction(meta.direction_matrix)])
    return facts


def zstd_header(path: Path) -> dict:
    """Return what a ``.pv`` file's metadata says, with its user dict but no array values.

    The metadata keeps each array's own dtype, so bool and complex arrays are reported as
    the full description reports them.
    """
    meta = zstd_metadata(path)
    kind = getattr(meta, 'ds_type', None)
    if kind == 'MultiBlock':
        info = zstd_composite_header(meta)
    elif kind in DATASET_TYPES:
        info = {'type': kind, 'facts': zstd_dataset_facts(meta, kind), 'rows': zstd_rows(meta)}
    else:
        return {}
    if getattr(meta, 'bounds', None) is not None:
        info['facts'].append(['Bounds', bounds(meta.bounds)])
    if USER_DICT_KEY in (meta.field_data_keys or {}):
        import pyvista_zstd

        with pyvista_zstd.Reader(path) as reader:
            values = zstd_array(reader, USER_DICT_KEY, 'Field')
        if values is not None:
            info['user_dict'] = user_dict_lines(values)
    return info


def zstd_rows(meta: Any) -> list[dict[str, Any]]:
    """Return the array rows of one ``.pv`` dataset's metadata, their ranges still to be found."""
    actives = {
        f'{association} {role}': getattr(meta, f'{association.lower()}_data_{attribute}')
        for association in ('Point', 'Cell')
        for role, attribute in ROLES
    }
    rows = []
    for association, attribute, _ in ASSOCIATIONS:
        for name, array in (getattr(meta, f'{attribute}_keys') or {}).items():
            if name == USER_DICT_KEY:
                continue
            dtype = np.dtype(array.dtype) if array.dtype not in ('str', 'object') else None
            is_text = dtype is None or dtype.kind in 'OUS'
            rows.append(
                header_row(
                    name,
                    association,
                    'string' if is_text else str(dtype),
                    components(list(array.shape)),
                    stored_roles(actives, association, name),
                )
            )
    return rows


def zstd_leaves(meta: Any, prefix: str = '') -> list[tuple[str, Any]]:
    """Return every dataset and empty block of a ``.pv`` composite, named by its path of names."""
    found = []
    for uid, name in zip(meta.children, meta.children_keys, strict=True):
        path = f'{prefix}{name}'
        child = meta.children_ds.get(uid)
        if getattr(child, 'ds_type', None) == 'MultiBlock':
            found += zstd_leaves(child, f'{path}/')
        else:
            found.append((path, child))
    return found


def zstd_composite_header(meta: Any) -> dict[str, Any]:
    """Return what a ``.pv`` composite's metadata says: its blocks, counts, and arrays."""
    found = zstd_leaves(meta)
    entries = []
    for name, child in found:
        dims = getattr(child, 'dimensions', None)
        entries.append(
            BlockEntry(
                name,
                child.ds_type if child is not None else block_kind(None),
                child.n_points if child is not None else None,
                child.n_cells if child is not None else None,
                dimensions(dims) if dims is not None else '',
            )
        )
    facts, blocks = composite_summary(
        len(meta.children),
        getattr(meta, 'n_points', None),
        getattr(meta, 'n_cells', None),
        entries,
    )
    datasets = [child for _, child in found if child is not None]
    return {
        'type': 'MultiBlock',
        'facts': facts,
        'rows': merge_rows([zstd_rows(child) for child in datasets]),
        'blocks': blocks,
        'block_count': len(found),
    }


def zstd_ranges(path: Path, header: dict, skip: set[tuple[str, str]] = frozenset()) -> dict:
    """Fill in a ``.pv`` header's ranges, reading each array alone, and return the header."""
    import pyvista_zstd

    with pyvista_zstd.Reader(path) as reader:
        for row in header['rows']:
            if (row['name'], row['association']) in skip:
                continue
            # One array at a time, so memory never holds more than the largest.
            values = zstd_array(reader, row['name'], row['association'])
            if values is None:
                continue
            if row['dtype'] == 'string':
                row['range'] = text_preview(values)
            else:
                row['extent'] = values_extent(values)
    return header


def xml_attributes(text: str) -> dict[str, str]:
    """Return the attributes of an XML tag, their values unescaped."""
    return {key: html.unescape(value) for key, value in re.findall(r'(\w+)="([^"]*)"', text)}


def xml_range(attributes: dict[str, str], kind_code: str, norm: str) -> Extent | None:
    """Return the range a header records as written, or None for VTK's inverted empty range."""
    low, high = attributes['RangeMin'], attributes['RangeMax']
    if float(low) > float(high):
        return None
    return low, high, 'f' if norm else kind_code, norm


def xml_counts(kind: str, head: str) -> list[list[Any]]:
    """Return the grid or piece counts a VTK XML header writes, as written."""
    facts: list[list[Any]] = []
    if (grid := re.search(rf'<{kind}\b([^>]*)>', head)) is not None:
        attributes = xml_attributes(grid.group(1))
        if 'WholeExtent' in attributes:
            facts.append(['Whole extent', attributes['WholeExtent'].strip()])
        if 'Spacing' in attributes:
            facts.append(['Spacing', triple(float(v) for v in attributes['Spacing'].split())])
    if kind not in XML_PIECE_COUNTS:
        return facts
    pieces = [xml_attributes(piece) for piece in re.findall(r'<Piece\b([^>]*)>', head)]
    if len(pieces) != 1:
        return [*facts, ['Pieces', count(len(pieces))]]
    for key, label in XML_PIECE_COUNTS[kind]:
        if key in pieces[0]:
            facts.append([label, count(int(pieces[0][key]))])
    return facts


def xml_header(path: Path) -> dict:
    """Return what a serial VTK XML file's header says, ranges included where VTK wrote them.

    PyVista stores bool arrays as UInt8 and complex ones as two Float64 components, naming
    them in field arrays of its own, so those are reported as stored.
    """
    head = xml_text(path, XML_TAGS_HEAD)
    # Inline data can push later arrays past the window, so only a whole header is used.
    if '<AppendedData' not in head and '</VTKFile>' not in head:
        return {}
    head = head.split('<AppendedData', 1)[0]
    match = re.search(r'<VTKFile\b([^>]*)>', head)
    kind = xml_attributes(match.group(1)).get('type') if match else None
    if kind not in DATASET_TYPES:
        return {}
    actives: dict[str, str | None] = {}
    rows: dict[tuple[str, str], dict[str, Any]] = {}
    section = None
    tags = 'PointData|CellData|FieldData|Points|Coordinates|Cells|Verts|Lines|Strips|Polys'
    pattern = rf'<(/?)({tags}|DataArray|Array)\b([^>]*?)/?>'
    for closing, tag, text in re.findall(pattern, head):
        attributes = xml_attributes(text)
        if tag in XML_SECTIONS:
            section = None if closing else XML_SECTIONS[tag]
            for role, key in (
                ('scalars', 'Scalars'),
                ('vectors', 'Vectors'),
                ('normals', 'Normals'),
                ('texture', 'TCoords'),
            ):
                if section and key in attributes:
                    actives[f'{section} {role}'] = attributes[key]
            continue
        if tag not in ('DataArray', 'Array'):
            if not closing:
                section = None
            continue
        name = attributes.get('Name')
        if section is None or not name or name == USER_DICT_KEY or (name, section) in rows:
            continue
        n_components = int(attributes.get('NumberOfComponents', 1))
        dtype = XML_DTYPES.get(attributes.get('type', ''), attributes.get('type', '').lower())
        row = header_row(name, section, dtype, n_components, [])
        if 'RangeMin' in attributes and 'RangeMax' in attributes and dtype != 'string':
            kind_code = 'f' if dtype.startswith('float') else 'b' if dtype == 'bool' else 'i'
            # VTK records a vector's range over its magnitude, which is said as such.
            norm = VECTOR_NORM if n_components > 1 else ''
            row['extent'] = xml_range(attributes, kind_code, norm)
        rows[(name, section)] = row
    for (name, association), row in rows.items():
        row['active'] = stored_roles(actives, association, name)
    # Listed as a dataset lists them, point arrays first, though a file puts field data first.
    order = [association for association, _, _ in ASSOCIATIONS]
    return {
        'type': kind,
        'facts': xml_counts(kind, head),
        'rows': sorted(rows.values(), key=lambda row: order.index(row['association'])),
    }


HEADERS = {'.pv': zstd_header, **dict.fromkeys(XML_HEADER_SUFFIXES, xml_header)}
# Formats whose header describes the dataset; the service keeps its own copy of this set.
HEADER_SUFFIXES = frozenset(HEADERS)


def header_info(source: str) -> dict[str, Any]:
    """Return what the info panel can show from a file's header alone, read in a moment."""
    path = Path(source)
    header: dict[str, Any] = {}
    reader = HEADERS.get(path.suffix.lower())
    if reader is not None and path.is_file():
        try:
            header = reader(path)
        except Exception as error:  # noqa: BLE001
            # Reported for the service's log; the panel then shows only the file's facts.
            sys.stderr.write(f'header skipped: {type(error).__name__}: {error}\n')
    info: dict[str, Any] = {'type': header.get('type', ''), 'from_header': True}
    if header:
        rows = header['rows']
        info['facts'] = header['facts']
        info['array_count'] = len(rows)
        info['arrays'] = format_rows(rows)
        if 'blocks' in header:
            info['blocks'] = header['blocks']
            info['block_count'] = header['block_count']
        if header.get('user_dict'):
            info['user_dict'] = header['user_dict']
    info['file'] = file_facts(path)
    return info


def merged_rows(loaded: list[dict[str, Any]], header: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the header's rows in its order, with the rows of the arrays that were read."""
    read = {(row['name'], row['association']): row for row in loaded}
    rows = [read.pop((row['name'], row['association']), row) for row in header.get('rows', [])]
    return rows + list(read.values())


def describe(dataset: Any, source: str, header: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return everything the info panel shows about a dataset read from a file.

    ``header`` describes the arrays a selective read left out.
    """
    info: dict[str, Any] = {'type': type(dataset).__name__}
    rows: list[dict[str, Any]] = []
    described = isinstance(dataset, pv.DataSet | pv.MultiBlock | pv.PartitionedDataSet)
    if isinstance(dataset, pv.MultiBlock | pv.PartitionedDataSet):
        found = leaves(dataset) if isinstance(dataset, pv.MultiBlock) else partitions(dataset)
        facts, blocks = composite_facts(dataset, found)
        info['blocks'] = blocks
        info['block_count'] = len(found)
        rows = merge_arrays(dataset, [b for _, b in found if b is not None])
    elif isinstance(dataset, pv.DataSet):
        facts = dataset_facts(dataset)
        rows = array_rows(dataset)
        if header:
            rows = merged_rows(rows, header)
    else:
        facts = []
    if described:
        info['array_count'] = len(rows)
        info['arrays'] = format_rows(rows)
    info['facts'] = facts
    if described:
        lines = user_dict(dataset) or (header or {}).get('user_dict')
        if lines:
            info['user_dict'] = lines
    info['file'] = file_facts(Path(source))
    return info
