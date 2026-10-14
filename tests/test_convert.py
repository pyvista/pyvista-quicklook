"""Tests for how scenes and descriptions are keyed, named, and asked of the exporter."""

from __future__ import annotations

from pathlib import Path
import sys

import pytest

from pyvista_quicklook import convert
from pyvista_quicklook import environment
from pyvista_quicklook import render


def test_scene_key_differs_from_the_image_key():
    """A file's scene and its rendered image are cached separately."""
    identity = ('/mesh.vtu', 1, 2)
    settings = {'window_size': [8, 8], 'max_scene_points': 100}
    assert convert.scene_key(identity, settings) != render.cache_key(identity, settings)


def test_scene_key_follows_the_point_budget():
    """Changing the point budget invalidates the scene."""
    identity = ('/mesh.vtu', 1, 2)
    base = {'max_scene_points': 100}
    assert convert.scene_key(identity, base) != convert.scene_key(
        identity, {**base, 'max_scene_points': 200}
    )


def test_scene_passes_a_zero_point_budget_through(tmp_path, monkeypatch):
    """A point budget of zero means no cap, and must not fall back to the default."""
    sample = tmp_path / 'mesh.vtu'
    sample.write_text('x')
    seen = {}

    def fake_run(config, command, **kwargs):
        seen['command'] = command
        Path(command[3]).write_bytes(b'ply')

    monkeypatch.setattr(convert.environment, 'run', fake_run)
    monkeypatch.setattr(convert.config_mod, 'CACHE_DIR', tmp_path / 'cache')
    settings = {'python': sys.executable, 'max_scene_points': 0, 'cache': False}
    out = convert.scene(sample, settings)
    command = seen['command']
    assert command[command.index('--max-points') + 1] == '0'
    assert out.read_bytes() == b'ply'


def test_scene_version_follows_the_exporter(tmp_path):
    """Any change to the exporter changes the version scenes are keyed and named by."""
    exporter = tmp_path / 'exporter.py'
    exporter.write_text('a')
    before = convert.converter_version(exporter)
    exporter.write_text('b')
    assert convert.converter_version(exporter) != before
    assert convert.converter_version(convert.EXPORTER, convert.DESCRIBER) == convert.SCENE_VERSION


def test_hidden_panel_skips_describing(tmp_path):
    """No description is asked of the exporter when the panel is hidden."""
    assert convert.describe(tmp_path / 'x.vtp', {'info_panel': 'hidden'}) is None
    assert convert.wants_info({})
    assert not convert.wants_info({'info_panel': 'hidden'})


def test_scene_reports_a_missing_file(tmp_path):
    """Converting a file that is not there is reported plainly."""
    with pytest.raises(environment.RenderError, match='No such file'):
        convert.scene(tmp_path / 'absent.vtu', {})


def test_scene_rejects_files_above_the_limit(tmp_path):
    """The interactive path honours the size limit before it looks for PyVista."""
    sample = tmp_path / 'big.vtu'
    sample.write_bytes(b'x')
    settings = {'max_file_size_mb': 1, 'python': str(tmp_path / 'absent')}
    with pytest.raises(environment.RenderError, match='preview limit'):
        convert.scene(sample, settings, identity=(str(sample), 0, 5 * 1024 * 1024))


def test_an_unreadable_description_is_none(tmp_path, monkeypatch):
    """A cached description that is not JSON leaves the panel out."""
    monkeypatch.setattr(convert.config_mod, 'CACHE_DIR', tmp_path)
    identity = ('/data/x.vtp', 1, 2)
    convert.info_path(identity).write_text('{not json')
    assert convert.describe('/data/x.vtp', {}, identity, read=False) is None


def test_descriptions_are_named_for_the_file_and_the_describer(monkeypatch, tmp_path):
    """A description is named by the describer's version and the file alone, not its preview."""
    monkeypatch.setattr(convert.config_mod, 'CACHE_DIR', tmp_path)
    first = convert.info_path(('/d/a.vtu', 1, 2))
    assert first.parent == tmp_path
    assert first.name.startswith(f'{convert.SCENE_VERSION}-')
    assert first.suffix == '.json'
    assert convert.info_path(('/d/a.vtu', 1, 2)) == first
    assert convert.info_path(('/d/a.vtu', 3, 2)) != first
