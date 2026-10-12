"""Build the README's format tables from PyVista's reader tables.

This module is run by the PyVista interpreter, not by ``pvql`` itself:
``python -m pyvista_quicklook._formats_table README.md`` rewrites the tables in place.
"""

from __future__ import annotations

from itertools import zip_longest
from pathlib import Path
import sys
from typing import NamedTuple

import meshio
from pyvista.core.utilities._optional_formats import _OPTIONAL_FORMATS
from pyvista.core.utilities.reader import CLASS_READERS
from pyvista.core.utilities.reader_registry import registered_readers

from .formats import FORMATS
from .formats import NATIVE_TYPES
from .formats import SYSTEM_TYPES
from .formats import UNCLAIMED
from .formats import VIA_SYSTEM_TYPE

START = '## Supported files\n'
END = 'Claims on an extension that another kind of file owns'


class Row(NamedTuple):
    """One format: its name and the extensions one reader takes."""

    name: str
    extensions: list[str]


def reader_of(ext: str) -> str:
    """Return what reads an extension: a VTK reader class, a plugin, or meshio's format."""
    for entry in registered_readers():
        if entry.extension == ext:
            return entry.source
    if ext in CLASS_READERS:
        return CLASS_READERS[ext].__name__
    if ext in meshio.extension_to_filetypes:
        return 'meshio:' + ','.join(meshio.extension_to_filetypes[ext])
    optional = _OPTIONAL_FORMATS.get(ext)
    return optional.module if optional is not None else ext


def rows() -> list[Row]:
    """Return one row per reader, so a format lists every extension it takes."""
    grouped: dict[str, list[str]] = {}
    for ext in FORMATS:
        grouped.setdefault(reader_of(ext), []).append(ext)
    result = []
    for extensions in grouped.values():
        names = {FORMATS[ext] for ext in extensions}
        if len(names) > 1:
            message = f'{extensions} share a reader but not a description'
            raise ValueError(message)
        result.append(Row(names.pop(), sorted(extensions)))
    return sorted(result, key=lambda row: row.name.lower())


def section() -> str:
    """Return the README text between the Supported files heading and the closing note."""
    claimed = rows()

    def cell(row: Row) -> str:
        return ', '.join(f'`{ext}`' for ext in row.extensions)

    def pair(left: Row, right: Row | None) -> str:
        tail = f' {right.name} | {cell(right)} |' if right else ' | |'
        return f'| {left.name} | {cell(left)} |{tail}'

    half = (len(claimed) + 1) // 2
    via: dict[str, list[str]] = {}
    for ext, uti in VIA_SYSTEM_TYPE.items():
        via.setdefault(uti, []).append(ext)
    reasons: dict[str, list[str]] = {}
    for ext, reason in UNCLAIMED.items():
        reasons.setdefault(reason, []).append(ext)
    lines = [
        '',
        f'{len(FORMATS)} extensions are claimed, so pressing space on any of these files opens',
        'this preview. A file that turns out not to be a mesh, such as a `.dat` holding a table',
        'of numbers, is shown as plain text instead, the way Quick Look would have shown it.',
        '',
        '| Format | Extensions | Format | Extensions |',
        '| --- | --- | --- | --- |',
        *(pair(left, right) for left, right in zip_longest(claimed[:half], claimed[half:])),
        '',
        (
            f'{len(VIA_SYSTEM_TYPE)} more are claimed through a type macOS declares, since'
            ' Launch Services sees only'
        ),
        'the last suffix of a compressed dataset:',
        '',
        *(
            f'- {SYSTEM_TYPES[uti]} (`{uti}`): ' + ', '.join(f'`{e}`' for e in sorted(exts))
            for uti, exts in via.items()
        ),
        '',
        'A folder of DICOM slices is claimed the same way, through `public.folder`.',
        '',
        (
            f'{len(NATIVE_TYPES)} more have a type and a preview of their own in macOS, which'
            ' Quick Look always uses;'
        ),
        'the app can open them instead (see [Native formats](#native-formats)):',
        '',
        *(f'- `{ext}` (`{uti}`)' for ext, uti in sorted(NATIVE_TYPES.items())),
        '',
        'PyVista can also read these, which are not claimed:',
        '',
        *(
            f'- {reason}: ' + ', '.join(f'`{e}`' for e in sorted(exts))
            for reason, exts in reasons.items()
        ),
        '',
        '',
    ]
    return '\n'.join(lines)


def rewrite(readme: Path) -> None:
    """Replace the tables in a README with freshly generated ones."""
    text = readme.read_text()
    head, rest = text.split(START, 1)
    _, tail = rest.split(END, 1)
    readme.write_text(head + START + section() + END + tail)


if __name__ == '__main__':
    rewrite(Path(sys.argv[1]))
