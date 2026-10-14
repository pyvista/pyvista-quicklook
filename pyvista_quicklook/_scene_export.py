"""Convert a mesh file into a coloured PLY that SceneKit can display.

This module is run by the PyVista interpreter, not by ``pvql`` itself, so it
imports only PyVista and its own dependencies.
"""

from __future__ import annotations

import argparse
import itertools
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any
import warnings

from matplotlib.colors import ListedColormap
import numpy as np
import pyvista as pv
from pyvista import _vtk

if __package__:
    from . import _dataset_info
else:
    import _dataset_info

# Opens the PLY comment holding the theme's shading and lighting, which SceneStyle reads.
STYLE_COMMENT = 'pvql style'
# The thinnest line drawn, in pixels, whatever the theme's line width.
MIN_LINE_WIDTH = 2.0
# Readers tried in turn for extensions two formats share; None is PyVista's own choice.
ATTEMPTS = {'.inp': (None, 'abaqus'), '.msh': (None, 'ansys')}


def apply_theme(spec: str | None) -> None:
    """Make a theme PyVista's own: a registered name, or the path of a theme saved as JSON."""
    if not spec:
        return
    if os.path.isabs(spec):
        with warnings.catch_warnings():
            # PyVista saves this flag with the theme, then refuses it when loading it back.
            warnings.filterwarnings('ignore', 'The jupyter_extension_available flag is read only')
            pv.global_theme.load_theme(spec)
    else:
        pv.set_plot_theme(spec)


def scene_style() -> dict[str, Any]:
    """Return how the theme shades and lights a mesh, as ``add_mesh`` would draw it."""
    theme = pv.global_theme
    light = theme.lighting_params
    flat = not theme.smooth_shading and light.interpolation.name == 'FLAT'
    return {
        'shading': 'flat' if flat else 'smooth',
        'lighting': bool(theme.lighting),
        'ambient': float(light.ambient),
        'diffuse': float(light.diffuse),
        'specular': float(light.specular),
        'specular_power': float(light.specular_power),
        'point_size': float(theme.point_size),
        # A one-pixel line is a hairline on a Retina display; draw no thinner than two.
        'line_width': max(float(theme.line_width), MIN_LINE_WIDTH),
    }


def read_iges(source: str) -> object:
    """Tessellate an IGES file with cascadio, the kernel pyvista-cad reads STEP with."""
    import cascadio

    # pyvista-cad's STEP default, so IGES and STEP previews are meshed alike.
    glb = cascadio.load(Path(source).read_bytes(), file_type='iges', tol_linear=0.1)
    if not glb:
        message = 'no shapes could be read from the IGES file'
        raise ValueError(message)
    with tempfile.TemporaryDirectory() as folder:
        model = Path(folder) / 'model.glb'
        model.write_bytes(glb)
        return pv.read(model)


# Formats Model I/O reads for the exporter, through the pvql-modelio tool the app carries.
MODELIO_SUFFIXES = ('.usd', '.usda', '.usdc', '.abc')
MODELIO_TOOL = 'pvql-modelio'
APP_TOOL_PATHS = (
    Path.home() / 'Applications' / 'PyVista Quick Look.app' / 'Contents' / 'MacOS' / MODELIO_TOOL,
    Path('/Applications') / 'PyVista Quick Look.app' / 'Contents' / 'MacOS' / MODELIO_TOOL,
    # The source tree's build folder, for a checkout and for the tests.
    Path(__file__).resolve().parents[1] / 'build' / MODELIO_TOOL,
)


def modelio_tool() -> Path:
    """Return the pvql-modelio tool, named by PVQL_MODELIO or found in the installed app."""
    named = os.environ.get('PVQL_MODELIO')
    for candidate in ([Path(named)] if named else []) + list(APP_TOOL_PATHS):
        if candidate.is_file():
            return candidate
    message = f'the {MODELIO_TOOL} tool is not installed; reinstall PyVista Quick Look'
    raise ImportError(message)


def read_modelio(source: str, **_: Any) -> pv.MultiBlock:
    """Read a USD or Alembic file with Model I/O, one block per mesh, named by its path."""
    tool = modelio_tool()
    with tempfile.TemporaryDirectory() as folder:
        completed = subprocess.run(
            [str(tool), source, folder], capture_output=True, text=True, check=False, timeout=600
        )
        if completed.returncode != 0:
            # The tool's own report comes last, after whatever Model I/O logged.
            lines = [line for line in completed.stderr.splitlines() if line.strip()]
            raise ValueError(lines[-1].strip() if lines else f'{MODELIO_TOOL} failed')
        manifest = json.loads((Path(folder) / 'manifest.json').read_text())
        blocks = pv.MultiBlock()
        for entry in manifest:
            blocks.append(pv.read(Path(folder) / entry['file']), entry['name'])
    return blocks


for _suffix in MODELIO_SUFFIXES:
    pv.register_reader(_suffix, read_modelio)


def read(source: str) -> object:
    """Read a file, trying each reader its extension may need until one returns points."""
    if Path(source).suffix.lower() in ('.iges', '.igs'):
        return read_iges(source)
    failure: BaseException | None = None
    dataset = None
    for file_format in ATTEMPTS.get(Path(source).suffix.lower(), (None,)):
        try:
            dataset = pv.read(source, file_format=file_format)
        except (Exception, SystemExit) as error:  # noqa: BLE001
            # meshio exits rather than raising when a format does not match.
            failure = error
            continue
        if getattr(dataset, 'n_points', 1) > 0:
            return dataset
    if dataset is None and failure is not None:
        raise failure
    return dataset


# Above this size an XML file is read with only the array it is coloured by; a ``.pv`` file
# always is, since its other arrays can be measured one at a time.
SELECTIVE_BYTES = 256 * 1024 * 1024


# Arrays VTK's filters and readers add for their own bookkeeping, which a file may carry.
BOOKKEEPING = frozenset(
    {
        'vtkGhostType',
        'vtkOriginalPointIds',
        'vtkOriginalCellIds',
        'vtkValidPointMask',
        'vtkCompositeIndex',
        'vtkBlockColors',
        'vtkProcessId',
    }
)
# What a chooser knows of an array: its name, association, and the roles it is active for.
Candidate = tuple[str, str, list[str]]


def pick_colour(arrays: list[Candidate]) -> tuple[str, str] | None:
    """Return the array to colour by: the active scalars, or a real array when those are VTK's."""
    active = next((array for array in arrays if 'scalars' in array[2]), None)
    if active is None:
        return None
    if active[0] not in BOOKKEEPING:
        return active[0], active[1]
    for association in ('Point', 'Cell'):
        for name, where, roles in arrays:
            # Normals are unit length everywhere, so they would colour the mesh one shade.
            if where == association and name not in BOOKKEEPING and 'normals' not in roles:
                return name, association
    return None


def colour_choice(rows: list[dict[str, Any]]) -> tuple[str, str] | None:
    """Return the array the header says a dataset is coloured by, as ``choose_scalars`` picks."""
    return pick_colour([(row['name'], row['association'], row['active']) for row in rows])


def read_selectively(source: str) -> tuple[object, dict[str, Any]] | None:
    """Read a dataset with only the array it is coloured by, and its header for the rest.

    Returns None when the format has no header to describe the rest, so it is read whole.
    """
    path = Path(source)
    suffix = path.suffix.lower()
    if suffix == '.pv':
        header = _dataset_info.zstd_header(path)
    elif suffix in _dataset_info.HEADERS and path.stat().st_size > SELECTIVE_BYTES:
        header = _dataset_info.xml_header(path)
    else:
        return None
    if not header:
        return None
    choice = colour_choice(header['rows'])
    point = {choice[0]} if choice and choice[1] == 'Point' else set()
    cell = {choice[0]} if choice and choice[1] == 'Cell' else set()
    if suffix == '.pv':
        import pyvista_zstd

        # Measured before the mesh is read, so the two are never in memory together.
        loaded = {(name, 'Point') for name in point} | {(name, 'Cell') for name in cell}
        _dataset_info.zstd_ranges(path, header, skip=loaded)
        with pyvista_zstd.Reader(path) as reader:
            reader.selected_point_arrays = point
            reader.selected_cell_arrays = cell
            dataset = reader.read()
    else:
        # Text has no range in the header, so string arrays are read too for their preview.
        for row in header['rows']:
            if row['dtype'] == 'string':
                {'Point': point, 'Cell': cell}.get(row['association'], set()).add(row['name'])
        reader = pv.get_reader(str(path))
        reader.disable_all_point_arrays()
        reader.disable_all_cell_arrays()
        for name in point:
            reader.enable_point_array(name)
        for name in cell:
            reader.enable_cell_array(name)
        dataset = reader.read()
    if choice:
        dataset.set_active_scalars(choice[0], preference=choice[1].lower())
    return dataset, header


# The exit status that tells the service the file holds no mesh, rather than that it failed.
NOT_A_MESH_STATUS = 3


class NotAMeshError(Exception):
    """Raised when a file cannot be read as a mesh, or holds nothing to show."""


def read_for_preview(source: str) -> tuple[object, dict[str, Any] | None]:
    """Read a dataset as little as its format allows, with the header that describes the rest."""
    if not os.path.isdir(source):
        # Raises for a missing or unreadable file, which VTK would only log.
        with Path(source).open('rb'):
            pass
    selective = None
    try:
        selective = read_selectively(source)
    except Exception as error:  # noqa: BLE001
        # Reported for the service's log; the file is then read whole.
        sys.stderr.write(f'selective read skipped: {summary(error)}\n')
    if selective is not None:
        return selective
    try:
        dataset = read(source), None
    except OSError, ImportError, MemoryError:
        # The file, the environment, or the machine is at fault, not the format.
        raise
    except (Exception, SystemExit) as error:
        if looks_like_text(source):
            raise NotAMeshError(summary(error)) from error
        raise
    if point_count(dataset[0]) == 0:
        raise empty(source)
    return dataset


def point_count(dataset: object) -> int:
    """Return how many points a dataset or composite holds, counting a mystery as some."""
    dataset = _dataset_info.as_multiblock(dataset)
    if isinstance(dataset, pv.MultiBlock):
        return sum(
            block.n_points for block in dataset.recursive_iterator('blocks', skip_none=True)
        )
    return int(getattr(dataset, 'n_points', 1))


def empty(source: str) -> Exception:
    """Return the error for a file read as a mesh that holds nothing to show."""
    message = 'the dataset has no surface to show'
    # Text with nothing in it is shown as text; a binary file with nothing is a failure.
    return NotAMeshError(message) if looks_like_text(source) else ValueError(message)


def looks_like_text(source: str) -> bool:
    """Return whether a file's opening reads as text, which is then worth showing as such."""
    try:
        with Path(source).open('rb') as handle:
            head = handle.read(8192)
    except OSError:
        return False
    if not head or b'\0' in head:
        return False
    data = np.frombuffer(head, dtype=np.uint8)
    controls = np.count_nonzero((data < 32) & ~np.isin(data, (9, 10, 13)))
    return controls * 100 < len(head)


def skin_estimate(dimensions: tuple[int, int, int]) -> int:
    """Return roughly how many points the outer surface of a structured dataset has."""
    extents = [k for k in dimensions if k > 1]
    if len(extents) == 3:
        a, b, c = extents
        return 2 * (a * b + b * c + a * c)
    return math.prod(extents)


def thin(dataset: object, max_points: int) -> object:
    """Stride structured data down to the budget; decimating it would take minutes."""
    if not max_points or not hasattr(dataset, 'extract_subset'):
        return dataset
    axes = sum(1 for k in dataset.dimensions if k > 1)
    budget = max_points // 3 if has_cell_scalars(dataset) else max_points
    expected = skin_estimate(dataset.dimensions)
    if expected <= budget:
        return dataset
    stride = math.ceil((expected / budget) ** (1 / min(axes, 2)))
    # The extent need not start at zero: a cropped image keeps its place in the original.
    return dataset.extract_subset(voi=dataset.extent, rate=(stride,) * 3)


def slice_volume(dataset: pv.DataSet) -> pv.DataSet:
    """Cut three slices through the centre of a volume that has scalars to show."""
    if not will_slice(dataset):
        return dataset
    return dataset.slice_orthogonal()


def will_slice(dataset: object) -> bool:
    """Return whether a volume is shown as slices through its centre."""
    dimensions = getattr(dataset, 'dimensions', None)
    if dimensions is None or min(dimensions) < 2:
        return False
    return bool(dataset.active_scalars_name)


def to_surface(dataset: object) -> pv.PolyData:
    """Return the surface of any dataset PyVista can read, cells left as they are."""
    dataset = _dataset_info.as_multiblock(dataset)
    if isinstance(dataset, pv.MultiBlock):
        # combine() fails on an empty nested composite, so only the leaves are joined.
        leaves = list(dataset.recursive_iterator('blocks', skip_none=True))
        if not leaves:
            return pv.PolyData()
        dataset = pv.MultiBlock(leaves).combine()
    if isinstance(dataset, pv.PointSet):
        # A PointSet has no cells to extract a surface from; its points are the whole of it.
        return dataset.cast_to_polydata(deep=False)
    if isinstance(dataset, pv.PolyData):
        return dataset
    if hasattr(dataset, 'extract_surface'):
        return dataset.extract_surface(
            algorithm='dataset_surface', pass_pointid=False, pass_cellid=False
        )
    return dataset.extract_geometry()


# Viridis at 32 control points, interpolated to a full ramp.
_VIRIDIS = (
    '440154470d6048186a482475472e7c4538824241863e4c8a3a548c365d8d32658e2e6d8e2b758e'
    '287d8e25848e228c8d1f948c1e9c8920a38625ab822eb37c3aba7648c16e58c76569cd5b7fd34e'
    '93d741a8db34bddf26d5e21aeae51afde725'
)


RAMP_SIZE = 256


def interpolated(control: np.ndarray) -> np.ndarray:
    """Return a full RGB ramp through evenly spaced control colours."""
    positions = np.linspace(0, RAMP_SIZE - 1, len(control))
    channels = [np.interp(np.arange(RAMP_SIZE), positions, control[:, c]) for c in range(3)]
    return np.stack(channels, axis=1).round().astype(np.uint8)


def ramp() -> np.ndarray:
    """Return the RGB table of the theme's colormap, one entry per scalar level."""
    cmap = pv.global_theme.cmap
    if isinstance(cmap, str) and cmap == 'viridis':
        return interpolated(np.frombuffer(bytes.fromhex(_VIRIDIS), dtype=np.uint8).reshape(-1, 3))
    if isinstance(cmap, list | tuple):
        # PyVista draws a list of colours as bands of just those colours.
        cmap = ListedColormap([pv.Color(c).float_rgb for c in cmap])
    table = pv.LookupTable(cmap=cmap, n_values=RAMP_SIZE)
    return np.asarray(table.values)[:, :3].astype(np.uint8)


def drawable(surface: pv.PolyData) -> tuple[pv.PolyData, str]:
    """Return what of a surface is drawn, and as which primitive: faces, lines or points."""
    if surface.n_faces or surface.n_strips:
        # Vertex and line cells come first in a PolyData, and only the faces are drawn.
        loose = surface.n_verts + surface.n_lines
        return (surface.remove_cells(np.arange(loose)) if loose else surface), 'faces'
    if surface.n_lines:
        drawn = segments(surface)
        # Line cells of a single point have no segment; their points still show.
        if drawn.n_faces:
            return drawn, 'lines'
    return keep_points(surface, slice(None)), 'points'


def keep_points(surface: pv.PolyData, kept: slice | np.ndarray) -> pv.PolyData:
    """Return a bare point cloud of the surface's points, carrying their data."""
    points = pv.PolyData(surface.points[kept])
    for name in surface.point_data:
        points.point_data[name] = np.asarray(surface.point_data[name])[kept]
    points.set_active_scalars(surface.active_scalars_name)
    return points


def segments(lines: pv.PolyData) -> pv.PolyData:
    """Return each segment of a mesh's lines as a face of three points, the first one twice.

    A PLY carries faces alone, and the scene draws a face that folds back on itself as a line.
    """
    faces: list[int] = []
    cells: list[int] = []
    connectivity = lines.lines
    offset = 0
    # Cell data lists the vertex cells first, then the lines.
    cell = lines.n_verts
    while offset < len(connectivity):
        count = int(connectivity[offset])
        ids = connectivity[offset + 1 : offset + 1 + count]
        for a, b in itertools.pairwise(ids):
            faces += [3, int(a), int(b), int(a)]
            cells.append(cell)
        offset += 1 + count
        cell += 1
    drawn = pv.PolyData(lines.points, faces=np.array(faces, dtype=np.int64))
    drawn.point_data.update(lines.point_data)
    for name in lines.cell_data:
        drawn.cell_data[name] = np.asarray(lines.cell_data[name])[cells]
    return drawn


def triangles(surface: pv.PolyData) -> pv.PolyData:
    """Return a surface made of triangles, as it is when it already is."""
    return surface if surface.is_all_triangles else surface.triangulate()


def recentre(surface: pv.PolyData) -> pv.PolyData:
    """Move the mesh to the origin, so far-from-origin coordinates keep their precision."""
    return surface.translate(-np.array(surface.center), inplace=False)


def choose_scalars(dataset: pv.DataSet) -> pv.DataSet:
    """Point the dataset away from VTK's bookkeeping arrays, colouring by a real one."""
    name = dataset.active_scalars_name
    if not name:
        # Nothing was active, so the file asks for no colour. Leave it that way.
        return dataset
    active = (name, dataset.active_scalars_info.association.name.title())
    arrays: list[Candidate] = []
    for association, data in (('Point', dataset.point_data), ('Cell', dataset.cell_data)):
        for candidate in data:
            roles = ['scalars'] if (candidate, association) == active else []
            if candidate == data.active_normals_name:
                roles.append('normals')
            arrays.append((candidate, association, roles))
    choice = pick_colour(arrays)
    if choice is None:
        dataset.set_active_scalars(None)
    elif choice != active:
        dataset.set_active_scalars(choice[0], preference=choice[1].lower())
    return dataset


def has_cell_scalars(dataset: pv.DataSet) -> bool:
    """Return whether the active scalars sit on cells rather than points."""
    name = dataset.active_scalars_name
    return bool(name) and name in dataset.cell_data


def to_point_scalars(surface: pv.PolyData) -> pv.PolyData:
    """Return a surface carrying cell scalars on points, one cell per point."""
    if not has_cell_scalars(surface):
        return surface
    return to_surface(surface.separate_cells().cell_data_to_point_data())


def colours_for(surface: pv.PolyData) -> np.ndarray | None:
    """Return per-point RGB for the active scalars, or None when there are none."""
    scalars = surface.active_scalars
    if scalars is None:
        return None

    scalars = np.asarray(scalars)
    if scalars.dtype.kind not in 'biuf':
        return None
    if scalars.ndim == 2:
        if scalars.shape[1] >= 3 and scalars.dtype == np.uint8:
            return scalars[:, :3]
        scalars = np.linalg.norm(scalars, axis=1)

    if scalars.ndim != 1 or scalars.size != surface.n_points:
        return None

    finite = scalars[np.isfinite(scalars)]
    if finite.size == 0:
        return None
    low, high = float(finite.min()), float(finite.max())
    span = high - low if high > low else 1.0
    missing = np.isnan(scalars)
    normalised = np.clip((np.nan_to_num(scalars, nan=low) - low) / span, 0.0, 1.0)
    colours = ramp()[(normalised * (RAMP_SIZE - 1)).round().astype(np.intp)]
    # PyVista draws a NaN value in the theme's colour for them, outside the ramp.
    colours[missing] = np.array(pv.global_theme.nan_color.int_rgb, dtype=np.uint8)
    return colours


def projected_points(surface: pv.PolyData) -> int:
    """Return how many points the scene will hold once cells are split."""
    return 3 * surface.n_faces if has_cell_scalars(surface) else surface.n_points


def write_info(
    dataset: object, source: str, destination: str, header: dict[str, Any] | None = None
) -> None:
    """Write what the info panel shows about a dataset; a failure leaves the panel out."""
    try:
        info = _dataset_info.describe(dataset, source, header)
    except Exception as error:  # noqa: BLE001
        sys.stderr.write(f'info skipped: {type(error).__name__}: {error}\n')
        return
    Path(destination).write_text(json.dumps(info))


def export(source: str, destination: str, max_points: int, info: str | None = None) -> None:
    """Write a mesh file out as a PLY with vertex colours; a budget of zero is no cap."""
    dataset, header = read_for_preview(source)
    if info:
        write_info(dataset, source, info, header)
    if isinstance(dataset, pv.DataSet):
        dataset = slice_volume(choose_scalars(thin(dataset, max_points)))
    # Triangulating first would discard the vertex and line cells drawable looks for.
    surface = choose_scalars(to_surface(dataset))
    wanted = surface.active_scalars_name
    surface, primitive = drawable(surface)
    if primitive == 'faces':
        surface = triangles(surface)
    surface.set_active_scalars(wanted if wanted in surface.array_names else None)
    if surface.n_points == 0:
        raise empty(source)

    if primitive == 'faces':
        if not isinstance(dataset, pv.PolyData):
            # The surface filter leaves a volume's faces unjoined at their edges.
            surface = surface.clean()
        projected = projected_points(surface)
        if max_points and projected > max_points:
            ratio = 1.0 - (max_points / projected)
            surface = surface.decimate_pro(ratio, preserve_topology=True)
    elif primitive == 'points' and max_points and surface.n_points > max_points:
        kept = np.arange(0, surface.n_points, surface.n_points // max_points + 1)
        surface = keep_points(surface, kept)

    surface = recentre(to_point_scalars(surface))
    colours = colours_for(surface)
    if colours is None:
        # PyVista draws a mesh without scalars in the theme's colour.
        colours = np.tile(
            np.array(pv.global_theme.color.int_rgb, dtype=np.uint8), (surface.n_points, 1)
        )
    normals = np.array(surface.point_data['Normals']) if 'Normals' in surface.point_data else None
    surface.clear_data()
    if normals is not None and len(normals) == surface.n_points:
        surface.point_data['Normals'] = normals
        surface.point_data.active_normals_name = 'Normals'
    surface['RGB'] = colours
    save_scene(surface, destination, primitive)


def save_scene(surface: pv.PolyData, destination: str, primitive: str = 'faces') -> None:
    """Write a binary PLY coloured by its ``RGB`` array, noting the theme's lighting."""
    writer = _vtk.vtkPLYWriter()
    writer.SetFileName(destination)
    writer.SetInputData(surface)
    writer.SetFileTypeToBinary()
    writer.SetArrayName('RGB')
    style = {**scene_style(), 'primitive': primitive}
    writer.AddComment(f'{STYLE_COMMENT} {json.dumps(style, separators=(",", ":"))}')
    if not writer.Write():
        message = f'could not write {destination}'
        raise OSError(message)


def summary(error: BaseException) -> str:
    """Return an error as one short line, named by its type."""
    text = ' '.join(str(error).split())[:200]
    return f'{type(error).__name__}: {text}' if text else type(error).__name__


def main() -> int:
    """Run the converter from the command line."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source')
    parser.add_argument('destination', nargs='?', help='the scene; leave out to only describe')
    parser.add_argument('--max-points', type=int, default=0)
    parser.add_argument('--info')
    parser.add_argument('--header', help='describe from the header alone, reading no values')
    parser.add_argument('--theme', help="a theme's name, or the path of a theme saved as JSON")
    args = parser.parse_args()
    if args.destination is None and args.info is None and args.header is None:
        parser.error('give a destination, --info, --header, or a combination')
    try:
        apply_theme(args.theme)
        if args.header is not None:
            Path(args.header).write_text(json.dumps(_dataset_info.header_info(args.source)))
        if args.destination is not None:
            export(args.source, args.destination, args.max_points, args.info)
        elif args.info is not None:
            dataset, header = read_for_preview(args.source)
            write_info(dataset, args.source, args.info, header)
    except Exception as error:
        # The reason is shown in the Quick Look panel, where a traceback would fill it.
        if os.environ.get('PVQL_TRACEBACK'):
            raise
        if isinstance(error, NotAMeshError):
            sys.stderr.write(f'{error}\n')
            return NOT_A_MESH_STATUS
        sys.stderr.write(f'{summary(error)}\n')
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
