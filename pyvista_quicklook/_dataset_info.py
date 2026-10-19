"""Describe a dataset for the info panel beside its preview, every value formatted."""

from __future__ import annotations

import contextlib
import html
import json
from pathlib import Path
import re
import sys
from typing import TYPE_CHECKING
from typing import Any
from typing import NamedTuple
import warnings

import numpy as np
import pyvista as pv
from pyvista import _vtk
from pyvista.core.utilities.arrays import convert_array

if TYPE_CHECKING:
    from collections.abc import Sequence

# Rows past these are summarised in a final line rather than listed.
MAX_ARRAYS = 40
MAX_BLOCKS = 40
# Bytes of a VTK XML file read when looking for its header and encoding.
XML_HEAD = 64 * 1024
# The norm a range was taken over: a complex modulus, or the magnitude VTK XML records.
COMPLEX_NORM = '||z||'
VECTOR_NORM = '||v||'

# Typographic separators, spelled as escapes so they cannot be mistaken for ASCII.
TIMES = '\u00d7'
THOUSANDS = '\u00a0'

# Raster formats whose single-slice images are shown with two dimensions.
FLAT_IMAGE_SUFFIXES = frozenset(
    {
        '.bmp',
        '.dcm',
        '.dem',
        '.hdr',
        '.jpeg',
        '.jpg',
        '.pgm',
        '.png',
        '.pnm',
        '.ppm',
        '.tif',
        '.tiff',
    }
)

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

ASSOCIATIONS = (('Point', 'point_data'), ('Cell', 'cell_data'), ('Field', 'field_data'))
# PyVista keeps the user dict and its own bookkeeping in arrays of this prefix.
PYVISTA_PREFIX = '_PYVISTA_'
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
    """Return three numbers as a comma separated list of floats."""
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


def bounds(dataset: Any) -> list[list[str]]:
    """Return a dataset's bounds as one minimum and maximum per axis."""
    b = dataset.bounds
    return [[axis, real(b[i]), real(b[i + 1])] for axis, i in (('x', 0), ('y', 2), ('z', 4))]


def cell_type_ids(dataset: Any) -> list[int]:
    """Return the distinct cell type ids of a dataset, named or not, in id order."""
    if hasattr(dataset, 'GetDistinctCellTypes'):
        types = _vtk.vtkCellTypes()
        dataset.GetDistinctCellTypes(types)
        ids = _vtk.vtk_to_numpy(types.GetCellTypesArray())
    elif hasattr(dataset, 'GetCellTypesArray'):
        ids = np.unique(_vtk.vtk_to_numpy(dataset.GetCellTypesArray()))
    else:
        ids = dataset.distinct_cell_types
    return sorted(int(t) for t in ids)


def cell_type_name(type_id: int) -> str:
    """Return a cell type's name, or its id when VTK has no name for it."""
    try:
        return pv.CellType(type_id).name.title().replace('_', ' ')
    except ValueError:
        return f'Type {type_id}'


def cell_types(dataset: Any) -> list[str] | None:
    """Return the distinct cell type names, in cell type order."""
    try:
        ids = cell_type_ids(dataset)
    except AttributeError:
        return None
    return [cell_type_name(t) for t in ids] or None


def bound(value: Any, kind: str) -> str:
    """Return one end of a range, as a float for float arrays and as written otherwise."""
    if kind == 'f':
        return real(value, 4)
    if kind == 'b':
        return str(bool(value))
    return str(int(value))


# An array's minimum, maximum, value kind, and the norm it was taken over, if any.
Extent = tuple[Any, Any, str, str]


def data_range(dataset: Any, name: str, association: str, array: np.ndarray) -> Extent | None:
    """Return an array's range as ``get_data_range`` gives it, or the magnitude's for vectors."""
    if array.size == 0 or array.dtype.kind not in 'biuf' or components(array.shape) > 1:
        return values_extent(array)
    with warnings.catch_warnings():
        # An array of only NaN has no range, which is shown rather than warned about.
        warnings.simplefilter('ignore', RuntimeWarning)
        low, high = dataset.get_data_range(name, preference=association.lower())
    return low, high, array.dtype.kind, ''


def components(shape: Sequence[int]) -> int:
    """Return how many components each tuple of an array of the given shape has."""
    return 1 if len(shape) < 2 else int(np.prod(shape[1:]))


def values_extent(array: np.ndarray) -> Extent | None:
    """Return an array's range from its values: of the magnitude for vectors, as VTK records it."""
    if array.size == 0:
        return None
    if array.dtype.kind == 'c':
        return modulus_extent(np.abs(array).ravel(), COMPLEX_NORM)
    if array.dtype.kind not in 'biuf':
        return None
    if components(array.shape) > 1:
        return magnitude_extent(array)
    with warnings.catch_warnings():
        warnings.simplefilter('ignore', RuntimeWarning)
        return np.nanmin(array), np.nanmax(array), array.dtype.kind, ''


def magnitude_extent(array: np.ndarray) -> Extent:
    """Return the range of each tuple's Euclidean norm as VTK measures it."""
    tuples = np.ascontiguousarray(array.reshape(len(array), -1))
    if tuples.dtype == bool:
        # VTK has no bool array; the XML formats store one as bytes too.
        tuples = tuples.view(np.uint8)
    low, high = convert_array(tuples).GetRange(-1)
    if no_range(low, high):
        return np.nan, np.nan, 'f', VECTOR_NORM
    return low, high, 'f', VECTOR_NORM


def modulus_extent(modulus: np.ndarray, norm: str) -> Extent:
    """Return the finite range of a magnitude or modulus, labelled with its norm."""
    finite = modulus[np.isfinite(modulus)]
    if finite.size == 0:
        return np.nan, np.nan, 'f', norm
    return finite.min(), finite.max(), 'f', norm


def merge_extents(first: Extent | None, second: Extent | None) -> Extent | None:
    """Return the range spanning two arrays of one name, or None when they cannot be combined."""
    if first is None or second is None or first[3] != second[3]:
        return None
    if first[2] != 'f' and second[2] != 'f':
        # Whole numbers stay whole, so no digit of a large integer is rounded away.
        kind = first[2] if first[2] == second[2] else 'i'
        return min(first[0], second[0]), max(first[1], second[1]), kind, first[3]
    with warnings.catch_warnings():
        warnings.simplefilter('ignore', RuntimeWarning)
        low = np.nanmin([float(first[0]), float(second[0])])
        high = np.nanmax([float(first[1]), float(second[1])])
    return low, high, 'f', first[3]


def no_range(low: Any, high: Any) -> bool:
    """Return whether a recorded range is the empty one VTK writes for arrays without values."""
    return not low <= high


def range_text(extent: Extent | None) -> str:
    """Return a range as text: one value when both ends agree, else an interval."""
    if extent is None:
        return ''
    low, high, kind, norm = extent
    if kind == 'f' and no_range(low, high):
        return 'NaN'
    low, high = bound(low, kind), bound(high, kind)
    text = low if low == high else f'[{low}, {high}]'
    return f'{norm} {text}' if norm else text


def active_roles(dataset: pv.DataSet, name: str, association: str) -> list[str]:
    """Return the roles an array is active for, as PyVista's own repr marks them."""
    if association == 'Field':
        return []
    roles = []
    # Scalars and vectors are active for one association of the dataset, not one per association.
    for role, attribute in (
        ('scalars', 'active_scalars_info'),
        ('vectors', 'active_vectors_info'),
    ):
        with contextlib.suppress(AttributeError, KeyError, ValueError):
            info = getattr(dataset, attribute)
            if info.name == name and info.association.name.title() == association:
                roles.append(role)
    data = getattr(dataset, dict(ASSOCIATIONS)[association])
    for role, attribute in (
        ('normals', 'active_normals_name'),
        ('texture', 'active_texture_coordinates_name'),
    ):
        with contextlib.suppress(AttributeError, KeyError, ValueError):
            if getattr(data, attribute) == name:
                roles.append(role)
    return roles


def array_rows(dataset: pv.DataSet) -> list[dict[str, Any]]:
    """Return one row per array, point arrays first, then cell and field arrays."""
    rows = []
    for association, attribute in ASSOCIATIONS:
        data = getattr(dataset, attribute)
        for name in data:
            if name.startswith(PYVISTA_PREFIX):
                continue
            array = np.asarray(data[name])
            is_text = array.dtype.kind in 'OUS'
            rows.append(
                {
                    'name': name,
                    'association': association,
                    'dtype': 'string' if is_text else str(array.dtype),
                    'components': components(array.shape),
                    'range': text_preview(array) if is_text else '',
                    'extent': None if is_text else data_range(dataset, name, association, array),
                    'active': active_roles(dataset, name, association),
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
    """Return the lines showing a dataset's PyVista user dict, or none when it has none."""
    if USER_DICT_KEY not in dataset.field_data:
        return []
    return user_dict_lines(np.asarray(dataset.field_data[USER_DICT_KEY]))


def user_dict_lines(stored: np.ndarray) -> list[str]:
    """Return the lines showing a user dict from the field array PyVista keeps it in."""
    try:
        value = json.loads(str(stored.ravel()[0]))
    except ValueError, IndexError:
        return []
    if not isinstance(value, dict) or not value:
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


def merge_arrays(blocks: list[pv.DataSet]) -> list[dict[str, Any]]:
    """Return array rows for a composite dataset, one per name and association."""
    return merge_rows([array_rows(block) for block in blocks])


def merge_rows(per_block: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Return one row per name and association from each block's array rows."""
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for block_rows in per_block:
        for row in block_rows:
            key = (row['name'], row['association'])
            if key not in merged:
                merged[key] = {**row, 'blocks': 1}
                continue
            seen = merged[key]
            seen['blocks'] += 1
            if seen['dtype'] != row['dtype']:
                seen['dtype'] = 'mixed'
            if seen['dtype'] == 'string':
                seen['range'] = ''
            seen['extent'] = merge_extents(seen['extent'], row['extent'])
    for row in merged.values():
        row['blocks'] = f'{row["blocks"]} of {len(per_block)}'
    return list(merged.values())


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


def dimensions(dataset: pv.DataSet, suffix: str) -> str:
    """Return a grid's dimensions, dropping the single slice of a flat image format."""
    shape = list(dataset.dimensions)
    if suffix in FLAT_IMAGE_SUFFIXES and shape[2] == 1:
        shape = shape[:2]
    return TIMES.join(str(d) for d in shape)


def dataset_facts(dataset: pv.DataSet, suffix: str = '') -> list[list[Any]]:
    """Return the label and value rows that describe one dataset."""
    facts = [
        ['Points', count(dataset.n_points)],
        ['Cells', count(dataset.n_cells)],
    ]
    if isinstance(dataset, pv.ImageData):
        facts.append(['Dimensions', dimensions(dataset, suffix)])
        facts.append(['Spacing', triple(dataset.spacing)])
        direction = np.asarray(dataset.direction_matrix)
        if not np.allclose(direction, np.eye(3)):
            facts.append(['Direction', 'rotated'])
    elif hasattr(dataset, 'dimensions'):
        facts.append(['Dimensions', dimensions(dataset, suffix)])
    if dataset.n_cells and (types := cell_types(dataset)) is not None:
        facts.append(['Cell type' if len(types) == 1 else 'Cell types', types])
    if dataset.n_points:
        facts.append(['Bounds', bounds(dataset)])
    return facts


def leaves(multiblock: pv.MultiBlock) -> list[tuple[str, pv.DataSet | None]]:
    """Return every dataset and None block in a composite, named by its nesting path."""
    return [
        (name or '/'.join(str(i) for i in index), block)
        for index, name, block in zip(
            multiblock.recursive_iterator('ids'),
            multiblock.recursive_iterator('names', prepend_names=True, separator='/'),
            multiblock.recursive_iterator('blocks', skip_none=False),
            strict=True,
        )
    ]


def block_kind(block: Any) -> str:
    """Return the type name of a block, an empty one named None."""
    return type(block).__name__ if block is not None else 'None'


def nesting_depth(multiblock: pv.MultiBlock) -> int:
    """Return how many levels of composite a composite holds, itself included."""
    nested = [nesting_depth(b) for b in multiblock if isinstance(b, pv.MultiBlock)]
    return 1 + max(nested, default=0)


def composite_facts(
    multiblock: pv.MultiBlock, found: list[tuple[str, pv.DataSet | None]]
) -> tuple[list[list[Any]], list[dict[str, Any]]]:
    """Return the summary rows and per-block rows of a composite, given its leaves."""
    entries = [
        BlockEntry(
            name,
            block_kind(block),
            block.n_points if block is not None else None,
            block.n_cells if block is not None else None,
            dimensions(block, '') if hasattr(block, 'dimensions') else '',
        )
        for name, block in found
    ]
    facts, blocks = composite_summary(entries, nesting_depth(multiblock))
    if any(block.n_points for _, block in found if block is not None):
        facts.append(['Bounds', bounds(multiblock)])
    return facts, blocks


class BlockEntry(NamedTuple):
    """One dataset or empty block of a composite, as its block list shows it."""

    name: str
    kind: str
    points: int | None
    cells: int | None
    dimensions: str


def composite_summary(
    found: list[BlockEntry], depth: int
) -> tuple[list[list[Any]], list[dict[str, Any]]]:
    """Return a composite's summary rows, bounds aside, and its per-block rows."""
    # Counted as the block list counts them: every dataset or empty block, at any depth.
    kinds: dict[str, int] = {}
    for entry in found:
        kinds[entry.kind] = kinds.get(entry.kind, 0) + 1
    facts = [['Blocks', count(len(found))]]
    if depth > 1:
        facts.append(['Depth', str(depth)])
    facts += [
        ['Points', count(sum(entry.points or 0 for entry in found))],
        ['Cells', count(sum(entry.cells or 0 for entry in found))],
    ]
    if kinds:
        label = 'Block type' if len(kinds) == 1 else 'Block types'
        facts.append(
            [label, '\n'.join(f'{count(kinds[k])} {k}' for k in sorted(kinds, key=str.lower))]
        )
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
    """Return the container version, compression, and uncompressed size of a ``.pv`` file."""
    import pyvista_zstd

    with pyvista_zstd.Reader(path) as reader:
        metadata = reader.file_metadata
        expanded = reader.nbytes
    ratio = expanded / path.stat().st_size
    return [
        ['Format', f'pyvista-zstd v{metadata.file_version}'],
        ['Compression', f'{metadata.compression} level {metadata.compression_level}'],
        ['Uncompressed', f'{size(expanded)} ({ratio:.2f}{TIMES} ratio)'],
    ]


def xml_facts(path: Path) -> list[list[str]]:
    """Return the version, compressor, and data encoding of a VTK XML file."""
    head = xml_text(path, XML_HEAD)
    match = re.search(r'<VTKFile\b([^>]*)>', head)
    if match is None:
        return []
    attributes = xml_attributes(match.group(1))
    facts = [['Format', f'VTK XML {attributes.get("version", "")}'.strip()]]
    encodings = sorted(set(re.findall(r'format="(\w+)"', head)))
    if 'appended' in encodings:
        appended = re.search(r'<AppendedData\b[^>]*encoding="(\w+)"', head)
        encodings[encodings.index('appended')] = (
            f'appended {appended.group(1)}' if appended else 'appended'
        )
    if encodings:
        facts.append(['Encoding', ', '.join(encodings)])
    compressor = attributes.get('compressor')
    # Text arrays are never compressed, whatever the header names.
    if compressor and encodings != ['ascii']:
        name = compressor.removeprefix('vtk').removesuffix('DataCompressor')
        facts.append(['Compression', COMPRESSORS.get(name, name)])
    return facts


def legacy_facts(path: Path) -> list[list[str]]:
    """Return the version, title, and encoding of a legacy VTK file."""
    with path.open('rb') as handle:
        lines = [handle.readline(256).decode('latin-1').strip() for _ in range(3)]
    version = lines[0].removeprefix('# vtk DataFile Version').strip()
    facts = [['Format', f'VTK legacy {version}'.strip()], ['Encoding', lines[2].lower()]]
    if lines[1] and not lines[1].lower().startswith('vtk output'):
        facts.append(['Title', lines[1]])
    return facts


def ply_facts(path: Path) -> list[list[str]]:
    """Return the encoding and comments in a PLY header."""
    facts = []
    comments = []
    with path.open('rb') as handle:
        for _ in range(200):
            line = handle.readline(1024).decode('latin-1').strip()
            if line.startswith('format '):
                encoding = line.split()[1].replace('_', ' ')
                facts.append(['Format', f'PLY {encoding}'])
            elif line.startswith(('comment ', 'obj_info ')):
                comments.append(line.split(' ', 1)[1])
            elif line == 'end_header' or not line:
                break
    if comments:
        facts.append(['Comment', comments[0][:80]])
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
# Header bytes searched for tags; a file whose tags run on past them is read whole.
XML_TAGS_HEAD = 8 * 1024 * 1024


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


def grid_counts(extent: list[int]) -> tuple[list[int], int, int]:
    """Return the dimensions, point count, and cell count of a structured extent."""
    dims = [extent[i + 1] - extent[i] + 1 for i in (0, 2, 4)]
    # One cell spans every axis of a single point, as VTK counts a one point grid.
    cells = int(np.prod([d - 1 for d in dims if d > 1])) if min(dims) > 0 else 0
    return dims, int(np.prod(dims)), cells


def header_facts(
    points: int, cells: int, dims: list[int] | None, spacing: list[float] | None
) -> list:
    """Return the facts a header gives: counts, and the shape of a grid."""
    facts: list[list[Any]] = [['Points', count(points)], ['Cells', count(cells)]]
    if dims is not None:
        facts.append(['Dimensions', TIMES.join(str(d) for d in dims)])
    if spacing is not None:
        facts.append(['Spacing', triple(spacing)])
    return facts


def scalar_roles(actives: dict[str, str | None], association: str, name: str) -> list[str]:
    """Return an array's roles from the active names a header records per association."""
    # PyVista makes point scalars active over cell scalars, and vectors the same way.
    roles = []
    for role in ('scalars', 'vectors'):
        point, cell = actives.get(f'Point {role}'), actives.get(f'Cell {role}')
        if (('Point', point) if point else ('Cell', cell)) == (association, name):
            roles.append(role)
    return roles + [
        role for role in ('normals', 'texture') if actives.get(f'{association} {role}') == name
    ]


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
        dims = list(meta.dimensions) if getattr(meta, 'dimensions', None) is not None else None
        spacing = meta.spacing if kind == 'ImageData' else None
        if dims is not None and kind != 'ImageData':
            dims = None
        info = {
            'type': kind,
            'facts': header_facts(meta.n_points, meta.n_cells, dims, spacing),
            'rows': zstd_rows(meta),
        }
    else:
        return {}
    if getattr(meta, 'bounds', None) is not None:
        info['facts'].append(['Bounds', bounds(meta)])
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
        f'{association} {role}': getattr(
            meta, f'{association.lower()}_data_active_{attribute}_name'
        )
        for association in ('Point', 'Cell')
        for role, attribute in (
            ('scalars', 'scalars'),
            ('vectors', 'vectors'),
            ('normals', 'normals'),
            ('texture', 'texture_coordinates'),
        )
    }
    rows = []
    for association, attribute in ASSOCIATIONS:
        for name, array in (getattr(meta, f'{attribute}_keys') or {}).items():
            if name.startswith(PYVISTA_PREFIX):
                continue
            dtype = np.dtype(array.dtype) if array.dtype not in ('str', 'object') else None
            is_text = dtype is None or dtype.kind in 'OUS'
            roles = [] if association == 'Field' else scalar_roles(actives, association, name)
            rows.append(
                header_row(
                    name,
                    association,
                    'string' if is_text else str(dtype),
                    components(list(array.shape)),
                    roles,
                )
            )
    return rows


def zstd_leaves(meta: Any, prefix: str = '') -> list[tuple[str, Any]]:
    """Return every dataset and empty block of a ``.pv`` composite, named by its nesting path."""
    found = []
    for index, (uid, name) in enumerate(zip(meta.children, meta.children_keys, strict=True)):
        path = f'{prefix}{name or index}'
        child = meta.children_ds.get(uid)
        if getattr(child, 'ds_type', None) == 'MultiBlock':
            found += zstd_leaves(child, f'{path}/')
        else:
            found.append((path, child))
    return found


def zstd_depth(meta: Any) -> int:
    """Return how many levels of composite a ``.pv`` composite holds, itself included."""
    nested = [
        zstd_depth(child)
        for child in meta.children_ds.values()
        if getattr(child, 'ds_type', None) == 'MultiBlock'
    ]
    return 1 + max(nested, default=0)


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
                TIMES.join(str(d) for d in dims) if dims is not None else '',
            )
        )
    facts, blocks = composite_summary(entries, zstd_depth(meta))
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


def xml_range(attributes: dict[str, str], kind_code: str, norm: str, tuples: int) -> Extent | None:
    """Return the range a header records, or the lack of one as the values would give it."""
    low, high = float(attributes['RangeMin']), float(attributes['RangeMax'])
    if not no_range(low, high):
        return low, high, 'f' if norm else kind_code, norm
    # VTK writes an inverted range for an array without values and for one that is all NaN.
    return (np.nan, np.nan, 'f', norm) if tuples and kind_code == 'f' else None


def xml_header(path: Path) -> dict:
    """Return what a serial VTK XML file's header says, ranges included where VTK wrote them.

    PyVista stores bool arrays as UInt8 and complex ones as two Float64 components, naming
    them only in arrays the header does not carry, so those are reported as stored.
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
    points = cells = 0
    dims: list[int] | None = None
    spacing: list[float] | None = None
    if (grid := re.search(rf'<{kind}\b([^>]*)>', head)) is not None:
        attributes = xml_attributes(grid.group(1))
        if 'WholeExtent' in attributes:
            dims, points, cells = grid_counts([int(v) for v in attributes['WholeExtent'].split()])
        if 'Spacing' in attributes:
            spacing = [float(v) for v in attributes['Spacing'].split()]
    if dims is None:
        for piece in re.finditer(r'<Piece\b([^>]*)>', head):
            attributes = xml_attributes(piece.group(1))
            points += int(attributes.get('NumberOfPoints', 0))
            parts = (
                ('NumberOfCells',)
                if kind != 'PolyData'
                else ('NumberOfVerts', 'NumberOfLines', 'NumberOfStrips', 'NumberOfPolys')
            )
            cells += sum(int(attributes.get(part, 0)) for part in parts)
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
        if (
            section is None
            or not name
            or name.startswith(PYVISTA_PREFIX)
            or (name, section) in rows
        ):
            continue
        n_components = int(attributes.get('NumberOfComponents', 1))
        dtype = XML_DTYPES.get(attributes.get('type', ''), attributes.get('type', '').lower())
        row = header_row(name, section, dtype, n_components, [])
        if 'RangeMin' in attributes and 'RangeMax' in attributes and dtype != 'string':
            kind_code = 'f' if dtype.startswith('float') else 'b' if dtype == 'bool' else 'i'
            # VTK records a vector's range over its magnitude, which is said as such.
            norm = VECTOR_NORM if n_components > 1 else ''
            tuples = {'Point': points, 'Cell': cells}.get(section, 1)
            tuples = int(attributes.get('NumberOfTuples', tuples))
            row['extent'] = xml_range(attributes, kind_code, norm, tuples)
        rows[(name, section)] = row
    for (name, association), row in rows.items():
        if association != 'Field':
            row['active'] = scalar_roles(actives, association, name)
    # Listed as a dataset lists them, point arrays first, though a file puts field data first.
    order = [association for association, _ in ASSOCIATIONS]
    return {
        'type': kind,
        'facts': header_facts(points, cells, dims, spacing),
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
    dataset = as_multiblock(dataset)
    rows: list[dict[str, Any]] = []
    if isinstance(dataset, pv.MultiBlock):
        found = leaves(dataset)
        facts, blocks = composite_facts(dataset, found)
        info['blocks'] = blocks
        info['block_count'] = len(found)
        rows = merge_arrays([b for _, b in found if b is not None])
    elif isinstance(dataset, pv.DataSet):
        facts = dataset_facts(dataset, Path(source).suffix.lower())
        rows = array_rows(dataset)
        if header:
            rows = merged_rows(rows, header)
    else:
        facts = []
    if isinstance(dataset, pv.DataSet | pv.MultiBlock):
        info['array_count'] = len(rows)
        info['arrays'] = format_rows(rows)
    info['facts'] = facts
    if isinstance(dataset, pv.DataSet | pv.MultiBlock):
        lines = user_dict(dataset) or (header or {}).get('user_dict')
        if lines:
            info['user_dict'] = lines
    info['file'] = file_facts(Path(source))
    return info
