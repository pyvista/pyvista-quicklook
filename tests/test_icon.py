"""The committed icon and docs logo are what icon/make_icon.py draws."""

from __future__ import annotations

from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).parents[1]
ICON = ROOT / 'icon'


@pytest.mark.skipif(
    shutil.which('uv') is None or shutil.which('iconutil') is None,
    reason='drawing the icon needs uv for its dependencies and iconutil for the .icns',
)
def test_the_committed_icns_is_what_the_script_draws(tmp_path):
    """Regenerating AppIcon.icns and the logo from pyvista.png gives the committed bytes."""
    output = tmp_path / 'AppIcon.icns'
    logo = tmp_path / 'logo.png'
    subprocess.run(
        ['uv', 'run', str(ICON / 'make_icon.py'), '--output', str(output), '--png', str(logo)],
        check=True,
        capture_output=True,
        timeout=300,
    )
    assert output.read_bytes() == (ICON / 'AppIcon.icns').read_bytes()
    assert logo.read_bytes() == (ROOT / 'doc' / '_static' / 'logo.png').read_bytes()
