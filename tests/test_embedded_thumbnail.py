"""Tests that the extension finds the picture a .pv file carries, and tells when it is stale."""

from __future__ import annotations

from pathlib import Path
import struct
import subprocess
import sys
import zlib

import pytest

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
    print("\\(picture.png.count) \\(picture.width)x\\(picture.height) stale=\\(picture.isStale)")
} else {
    print("none")
}
print(EmbeddedThumbnail.read(url, allowStale: false) == nil ? "declined" : "current")
"""


@pytest.fixture(scope='module')
def reader(tmp_path_factory):
    """Compile a tool that reports what EmbeddedThumbnail finds in a file."""
    folder = tmp_path_factory.mktemp('reader')
    (folder / 'main.swift').write_text(MAIN)
    tool = folder / 'read-thumbnail'
    subprocess.run(
        ['swiftc', str(READER), str(folder / 'main.swift'), '-o', str(tool)],
        check=True,
        capture_output=True,
    )
    return lambda path: subprocess.run(
        [str(tool), str(path)], check=True, capture_output=True, text=True
    ).stdout.split('\n')[:2]


def pv_file(path, *, index=b'i' * 64, crc_of=None, record_extra=0):
    """Write a file laid out as a .pv with a picture, ending in a four-frame index."""
    entries = crc_of if crc_of is not None else index
    # The checksum covers the index entries after the first two frames.
    crc = zlib.crc32(entries[32:])
    descriptor = b'PVZTHUMB' + struct.pack('<6I', 1, 32, 64, 48, len(PNG), crc)
    record = descriptor + PNG
    data = PREFIX + struct.pack('<I', len(record) + record_extra) + record
    data += b'frames' + index + struct.pack('<Q', len(index) // 16)
    path.write_bytes(data)
    return path


def test_a_current_picture_is_found(reader, tmp_path):
    """The picture, its size, and a matching checksum are read from where the format puts them."""
    found, current = reader(pv_file(tmp_path / 'mesh.pv'))
    assert found == f'{len(PNG)} 64x48 stale=false'
    assert current == 'current'


def test_a_picture_older_than_the_data_is_stale(reader, tmp_path):
    """Data appended after the picture changes the index, so the picture is stale."""
    found, current = reader(pv_file(tmp_path / 'mesh.pv', crc_of=b'o' * 64))
    assert found.endswith('stale=true')
    assert current == 'declined'


@pytest.mark.parametrize('record_extra', [1, -1])
def test_a_record_of_the_wrong_length_is_ignored(reader, tmp_path, record_extra):
    """A frame whose length disagrees with its descriptor holds no picture the reader trusts."""
    assert reader(pv_file(tmp_path / 'mesh.pv', record_extra=record_extra)) == ['none', 'declined']


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
