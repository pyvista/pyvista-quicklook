"""Describe a dataset for the info panel beside its preview, every value formatted."""

from __future__ import annotations

import contextlib
import json
from pathlib import Path
import re
import sys
from typing import Any
import warnings

import numpy as np
import pyvista as pv

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


def count(value: int) -> str:
    """Return an integer with spaces between thousands that never break across lines."""
    return f'{value:,}'.replace(',', THOUSANDS)


def number(value: float) -> str:
    """Return a coordinate or spacing short enough for a narrow column."""
    value = float(value)
    if value == 0:
        return '0'
    if value.is_integer() and abs(value) < 1e9:
        return str(int(value))
    return f'{value:.4g}'.replace('e+0', 'e').replace('e-0', 'e-').replace('e+', 'e')


def triple(values: Any) -> str:
    """Return three numbers as a comma separated list of floats."""
    return ', '.join(real(v) for v in values)


def size(nbytes: float) -> str:
    """Return a byte count the way the Finder shows one."""
    for unit in ('bytes', 'KB', 'MB', 'GB'):
        if nbytes < 1000:
            return f'{nbytes:.0f} {unit}' if unit == 'bytes' else f'{nbytes:.3g} {unit}'
        nbytes /= 1000
    return f'{nbytes:.3g} TB'


def real(value: float, digits: int = 6) -> str:
    """Return a number as a float to the given significant figures, always with a decimal point."""
    text = f'{float(value):.{digits}g}'
    return text if any(c in text for c in '.ein') else f'{text}.0'


def bounds(dataset: Any) -> list[list[str]]:
    """Return a dataset's bounds as one minimum and maximum per axis."""
    b = dataset.bounds
    return [[axis, real(b[i]), real(b[i + 1])] for axis, i in (('x', 0), ('y', 2), ('z', 4))]


def cell_types(dataset: pv.DataSet) -> list[str] | None:
    """Return the distinct cell type names, in cell type order."""
    with contextlib.suppress(Exception):
        types = sorted(dataset.distinct_cell_types)
        return [pv.CellType(t).name.title().replace('_', ' ') for t in types] or None
    return None


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
    if array.size == 0 or array.dtype.kind not in 'biuf' or components(array) > 1:
        return values_extent(array)
    with warnings.catch_warnings():
        # An array of only NaN has no range, which is shown rather than warned about.
        warnings.simplefilter('ignore', RuntimeWarning)
        low, high = dataset.get_data_range(name, preference=association.lower())
    return low, high, array.dtype.kind, ''


def components(array: np.ndarray) -> int:
    """Return how many components each tuple of an array has."""
    return 1 if array.ndim < 2 else int(np.prod(array.shape[1:]))


def values_extent(array: np.ndarray) -> Extent | None:
    """Return an array's range from its values: of the magnitude for vectors, as VTK records it."""
    if array.size == 0:
        return None
    if array.dtype.kind == 'c':
        return modulus_extent(np.abs(array).ravel(), COMPLEX_NORM)
    if array.dtype.kind not in 'biuf':
        return None
    if components(array) > 1:
        return modulus_extent(magnitude(array), VECTOR_NORM)
    with warnings.catch_warnings():
        warnings.simplefilter('ignore', RuntimeWarning)
        return np.nanmin(array), np.nanmax(array), array.dtype.kind, ''


def magnitude(array: np.ndarray) -> np.ndarray:
    """Return the Euclidean norm of each tuple of an array."""
    return np.linalg.norm(array.reshape(len(array), -1).astype(np.float64), axis=1)


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
    kind = first[2] if first[2] == second[2] else 'f'
    with warnings.catch_warnings():
        warnings.simplefilter('ignore', RuntimeWarning)
        low = np.nanmin([float(first[0]), float(second[0])])
        high = np.nanmax([float(first[1]), float(second[1])])
    return low, high, kind, first[3]


def range_text(extent: Extent | None) -> str:
    """Return a range as text: one value when both ends agree, else an interval."""
    if extent is None:
        return ''
    low, high, kind, norm = extent
    if kind == 'f' and np.isnan(low):
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
        with contextlib.suppress(Exception):
            info = getattr(dataset, attribute)
            if info.name == name and info.association.name.title() == association:
                roles.append(role)
    data = getattr(dataset, dict(ASSOCIATIONS)[association])
    for role, attribute in (
        ('normals', 'active_normals_name'),
        ('texture', 'active_texture_coordinates_name'),
    ):
        with contextlib.suppress(Exception):
            if getattr(data, attribute) == name:
                roles.append(role)
    return roles


def array_rows(dataset: pv.DataSet) -> list[dict[str, Any]]:
    """Return one row per array, point arrays first, then cell and field arrays."""
    rows = []
    for association, attribute in ASSOCIATIONS:
        data = getattr(dataset, attribute)
        for name in data:
            if name == USER_DICT_KEY:
                continue
            array = np.asarray(data[name])
            is_text = array.dtype.kind in 'OUS'
            rows.append(
                {
                    'name': name,
                    'association': association,
                    'dtype': 'string' if is_text else str(array.dtype),
                    'components': components(array),
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
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for block in blocks:
        for row in array_rows(block):
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
        row['blocks'] = f'{row["blocks"]} of {len(blocks)}'
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


def block_kinds(multiblock: pv.MultiBlock) -> tuple[dict[str, int], int]:
    """Return the count of each block type at every level, and the nesting depth."""
    kinds: dict[str, int] = {}
    depth = 1
    for block in multiblock:
        kind = type(block).__name__ if block is not None else 'None'
        kinds[kind] = kinds.get(kind, 0) + 1
        if isinstance(block, pv.MultiBlock):
            nested, levels = block_kinds(block)
            for name, n in nested.items():
                kinds[name] = kinds.get(name, 0) + n
            depth = max(depth, levels + 1)
    return kinds, depth


def composite_facts(multiblock: pv.MultiBlock) -> tuple[list[list[Any]], list[dict[str, Any]]]:
    """Return the summary rows and per-block rows of a composite dataset."""
    found = leaves(multiblock)
    datasets = [block for _, block in found if block is not None]
    kinds, depth = block_kinds(multiblock)
    # Counted as the block list counts them: every dataset or empty block, at any depth.
    facts = [['Blocks', count(len(found))]]
    if depth > 1:
        facts.append(['Depth', str(depth)])
    facts += [
        ['Points', count(sum(b.n_points for b in datasets))],
        ['Cells', count(sum(b.n_cells for b in datasets))],
    ]
    if kinds:
        label = 'Block type' if len(kinds) == 1 else 'Block types'
        facts.append(
            [label, '\n'.join(f'{count(kinds[k])} {k}' for k in sorted(kinds, key=str.lower))]
        )
    if any(b.n_points for b in datasets):
        facts.append(['Bounds', bounds(multiblock)])
    blocks = [
        {
            'name': name,
            'type': type(block).__name__ if block is not None else 'None',
            'points': count(block.n_points) if block is not None else '',
            'cells': count(block.n_cells) if block is not None else '',
            'dimensions': dimensions(block, '') if hasattr(block, 'dimensions') else '',
        }
        for name, block in found[:MAX_BLOCKS]
    ]
    if len(found) > MAX_BLOCKS:
        hidden = len(found) - MAX_BLOCKS
        blocks.append({'name': f'{ELLIPSIS_TEXT} {hidden} more blocks', 'more': True})
    return facts, blocks


def zstd_facts(path: Path) -> list[list[str]]:
    """Return the container version, compression, and uncompressed size of a ``.pv`` file."""
    import pyvista_zstd

    with pyvista_zstd.Reader(path) as reader:
        # The version and compression have no public accessor as of pyvista-zstd 0.5.
        metadata = reader._metadata
        expanded = reader.nbytes
    ratio = expanded / path.stat().st_size
    return [
        ['Format', f'pyvista-zstd v{metadata.file_version}'],
        ['Compression', f'{metadata.compression} level {metadata.compression_level}'],
        ['Uncompressed', f'{size(expanded)} ({ratio:.2f}{TIMES} ratio)'],
    ]


def xml_facts(path: Path) -> list[list[str]]:
    """Return the version, compressor, and data encoding of a VTK XML file."""
    with path.open('rb') as handle:
        head = handle.read(XML_HEAD).decode('latin-1')
    match = re.search(r'<VTKFile\b([^>]*)>', head)
    if match is None:
        return []
    attributes = dict(re.findall(r'(\w+)="([^"]*)"', match.group(1)))
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
XML_SUFFIXES = (
    '.vtu',
    '.vtp',
    '.vti',
    '.vtr',
    '.vts',
    '.vtm',
    '.vthb',
    '.vtpd',
    '.vtpc',
    '.pvtu',
    '.pvtp',
)


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


def describe(dataset: Any, source: str) -> dict[str, Any]:
    """Return everything the info panel shows about a dataset read from a file."""
    info: dict[str, Any] = {'type': type(dataset).__name__}
    if isinstance(dataset, pv.PartitionedDataSet):
        dataset = pv.MultiBlock(list(dataset))
    rows: list[dict[str, Any]] = []
    if isinstance(dataset, pv.MultiBlock):
        facts, blocks = composite_facts(dataset)
        info['blocks'] = blocks
        info['block_count'] = len(leaves(dataset))
        rows = merge_arrays([b for _, b in leaves(dataset) if b is not None])
    elif isinstance(dataset, pv.DataSet):
        facts = dataset_facts(dataset, Path(source).suffix.lower())
        rows = array_rows(dataset)
    else:
        facts = []
    if isinstance(dataset, pv.DataSet | pv.MultiBlock):
        info['array_count'] = len(rows)
        info['arrays'] = format_rows(rows)
    info['facts'] = facts
    if isinstance(dataset, pv.DataSet | pv.MultiBlock):
        lines = user_dict(dataset)
        if lines:
            info['user_dict'] = lines
    info['file'] = file_facts(Path(source))
    return info
