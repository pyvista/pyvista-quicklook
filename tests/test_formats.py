"""Tests for the table of formats the app claims."""

from __future__ import annotations

from pathlib import Path
import re

from pyvista_quicklook import formats


def test_normalize_accepts_any_spelling():
    """Extensions are lowercased and given exactly one leading dot."""
    assert formats.normalize('VTU') == '.vtu'
    assert formats.normalize('.VtU ') == '.vtu'


def test_every_known_format_is_claimed_by_default():
    """The format table and the default claims are the same set, and exclude macOS's own."""
    assert set(formats.default_extensions()) == set(formats.FORMATS)
    assert '.vtu' in formats.FORMATS
    assert '.stl' not in formats.FORMATS
    assert set(formats.FORMATS).isdisjoint(formats.UNCLAIMED)


def test_readme_lists_exactly_the_known_formats():
    """The README's Supported files section names every format and every unclaimed one."""
    readme = (Path(__file__).parents[1] / 'README.md').read_text()
    section = readme.split('## Supported files', 1)[1].split('\n## ', 1)[0]
    listed = set(re.findall(r'`(\.[a-z0-9.]+)`', section))
    known = set(formats.FORMATS) | set(formats.UNCLAIMED) | set(formats.VIA_SYSTEM_TYPE)
    assert listed == known | set(formats.NATIVE_TYPES)


def test_unclaimed_extensions_say_why():
    """Every extension left unclaimed carries a reason."""
    assert all(formats.UNCLAIMED.values())


def test_uti_is_unique_per_extension():
    """No two extensions share a uniform type identifier."""
    utis = [formats.uti_for(ext) for ext in formats.FORMATS]
    assert len(utis) == len(set(utis))
