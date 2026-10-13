"""Tests that the app finds the folder the render service watches."""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys

import pytest

from pyvista_quicklook import plist

ROOT = Path(__file__).resolve().parent.parent
HELPER = ROOT / 'macos' / 'Shared' / 'Helper.swift'

pytestmark = pytest.mark.skipif(sys.platform != 'darwin', reason='the helper is Swift')

MAIN = """
import Foundation
print(Helper.previewExtension(in: URL(fileURLWithPath: CommandLine.arguments[1])) ?? "none")
"""


@pytest.fixture(scope='module')
def finder(tmp_path_factory):
    """Compile a tool that reports which embedded extension the app sends requests through."""
    folder = tmp_path_factory.mktemp('helper')
    (folder / 'main.swift').write_text(MAIN)
    tool = folder / 'find-extension'
    subprocess.run(
        ['swiftc', str(HELPER), str(folder / 'main.swift'), '-o', str(tool)],
        check=True,
        capture_output=True,
    )
    return lambda path: subprocess.run(
        [str(tool), str(path)], check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.mark.parametrize(
    'names', [('a', 'b'), ('b', 'a')], ids=['preview first', 'thumbnails first']
)
def test_requests_go_through_the_preview_extension(finder, tmp_path, names):
    """Whatever order the extensions are listed in, requests go through the preview one."""
    infos = [plist.extension_plist(['.vtk']), plist.thumbnail_plist(['.pv'])]
    for name, info in zip(names, infos, strict=True):
        plist.write(info, tmp_path / f'{name}.appex' / 'Contents' / 'Info.plist')
    assert finder(tmp_path) == plist.EXT_BUNDLE_ID
    assert finder(tmp_path / 'missing') == 'none'
