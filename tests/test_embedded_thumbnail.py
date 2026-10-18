"""Tests that the extension finds the picture a .pv file carries, and tells when it is stale."""

from __future__ import annotations

import io
from pathlib import Path
import struct
import subprocess
import sys
import zlib

from conftest import compiled
import numpy as np
from PIL import Image
import pytest
import pyvista as pv
import pyvista_zstd

ROOT = Path(__file__).resolve().parent.parent
READER = ROOT / 'macos' / 'Shared' / 'EmbeddedThumbnail.swift'

pytestmark = pytest.mark.skipif(sys.platform != 'darwin', reason='the reader is Swift')

# The opening of every .pv file with a picture, as EmbeddedThumbnail.prefix spells it.
PREFIX = (
    bytes([0x28, 0xB5, 0x2F, 0xFD, 0xA0, 0x2B, 0, 0, 0, 0x59, 0x01, 0, 0x0B, 0, 0, 0])
    + b'__thumbnail'
    + bytes([1] + [0] * 11)
    + b'|u1'
    + b' ' * 13
    + bytes([0x50, 0x2A, 0x4D, 0x18])
)
PNG = b'\x89PNG\r\n\x1a\n' + b'picture'
MAIN = """
import Foundation
let url = URL(fileURLWithPath: CommandLine.arguments[1])
if let picture = EmbeddedThumbnail.picture(url) {
    print("\\(picture.png.count) stale=\\(picture.isStale)")
} else {
    print("none")
}
print(EmbeddedThumbnail.current(url) == nil ? "declined" : "current")
"""


@pytest.fixture(scope='module')
def reader():
    """Compile a tool that reports what EmbeddedThumbnail finds in a file."""
    tool = compiled('read-thumbnail', (READER,), main=MAIN)
    return lambda path: subprocess.run(
        [str(tool), str(path)], check=True, capture_output=True, text=True
    ).stdout.split('\n')[:2]


def pv_file(path, *, rest=b'i' * 32, crc_of=None, record_extra=0, png=PNG, index=None):
    """Write a file laid out as a .pv with a picture, ending in an index of its frames.

    The index holds the picture's two frames, then ``rest``; ``index`` replaces it whole.
    """
    # The checksum covers the index entries after the first two frames.
    crc = zlib.crc32(crc_of if crc_of is not None else rest)
    descriptor = b'PVZTHUMB' + struct.pack('<6I', 1, 32, 64, 48, len(png), crc)
    record = descriptor + png
    data = PREFIX + struct.pack('<I', len(record) + record_extra) + record
    if index is None:
        # Each entry is a frame's end offset and decompressed size.
        index = struct.pack('<4Q', len(PREFIX) - 4, 43, len(data), 0) + rest
    data += b'frames' + index + struct.pack('<Q', len(index) // 16)
    path.write_bytes(data)
    return path


def test_a_current_picture_is_found(reader, tmp_path):
    """The picture, its size, and a matching checksum are read from where the format puts them."""
    found, current = reader(pv_file(tmp_path / 'mesh.pv'))
    assert found == f'{len(PNG)} stale=false'
    assert current == 'current'


def test_a_picture_older_than_the_data_is_stale(reader, tmp_path):
    """Data appended after the picture changes the index, so the picture is stale."""
    found, current = reader(pv_file(tmp_path / 'mesh.pv', crc_of=b'o' * 32))
    assert found.endswith('stale=true')
    assert current == 'declined'


@pytest.mark.parametrize('record_extra', [1, -1])
def test_a_record_of_the_wrong_length_is_ignored(reader, tmp_path, record_extra):
    """A frame whose length disagrees with its descriptor holds no picture the reader trusts."""
    assert reader(pv_file(tmp_path / 'mesh.pv', record_extra=record_extra)) == ['none', 'declined']


@pytest.mark.parametrize(
    'png', [b'GIF89a' + b'picture', PNG + bytes(8 * 1024 * 1024)], ids=['not a png', 'too large']
)
def test_a_picture_that_is_not_a_small_png_is_ignored(reader, tmp_path, png):
    """Only a PNG under the size cap is taken, so a damaged length never reads the whole file."""
    assert reader(pv_file(tmp_path / 'mesh.pv', png=png)) == ['none', 'declined']


def test_a_truncated_file_holds_no_picture(reader, tmp_path):
    """A file cut short inside the picture is not read past its end."""
    path = pv_file(tmp_path / 'mesh.pv')
    path.write_bytes(path.read_bytes()[: len(PREFIX) + 40])
    assert reader(path) == ['none', 'declined']


def test_other_files_hold_no_picture(reader, tmp_path):
    """A file that does not open with the prefix, or is not a .pv, is passed over."""
    other = tmp_path / 'mesh.pv'
    other.write_bytes(b'\x28\xb5\x2f\xfd' + b'\0' * 200)
    assert reader(other) == ['none', 'declined']
    renamed = pv_file(tmp_path / 'mesh.vtu')
    assert reader(renamed) == ['none', 'declined']


@pytest.mark.parametrize('index', [b'', b'i' * 16], ids=['no frames', 'one frame'])
def test_an_index_without_the_picture_frame_holds_no_picture(reader, tmp_path, index):
    """An index too short to say where the picture's frame ends holds no picture."""
    pv_file(tmp_path / 'mesh.pv', index=index)
    assert reader(tmp_path / 'mesh.pv') == ['none', 'declined']


def test_a_pair_alone_is_a_stale_picture(reader, tmp_path):
    """With no frames after the picture, there is nothing for its checksum to vouch for."""
    pv_file(tmp_path / 'mesh.pv', rest=b'')
    assert reader(tmp_path / 'mesh.pv') == [f'{len(PNG)} stale=true', 'declined']


@pytest.mark.parametrize('shift', [-1, 1, 100])
def test_a_record_that_does_not_end_with_its_frame_is_ignored(reader, tmp_path, shift):
    """A record the index places elsewhere could read into other frames, so it holds no picture."""
    path = pv_file(tmp_path / 'mesh.pv')
    data = bytearray(path.read_bytes())
    entry = len(data) - 8 - 16 * 4 + 16
    end = struct.unpack_from('<Q', data, entry)[0]
    struct.pack_into('<Q', data, entry, end + shift)
    path.write_bytes(bytes(data))
    assert reader(path) == ['none', 'declined']


def test_a_picture_written_by_pyvista_zstd_is_found(reader, tmp_path):
    """The reader agrees with the writer whose format it mirrors, fresh and after an append."""
    buffer = io.BytesIO()
    Image.new('RGBA', (4, 3), (255, 0, 0, 255)).save(buffer, format='PNG')
    png = buffer.getvalue()
    path = tmp_path / 'sphere.pv'
    pyvista_zstd.write(pv.Sphere(), path, thumbnail=png)
    assert reader(path) == [f'{len(png)} stale=false', 'current']
    pyvista_zstd.append_arrays(path, {'extra': np.arange(3.0)})
    assert reader(path) == [f'{len(png)} stale=true', 'declined']
