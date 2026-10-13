"""Tests for running work in the PyVista environment and caching what it builds."""

from __future__ import annotations

import sys

import pytest

from pyvista_quicklook import cache
from pyvista_quicklook import config
from pyvista_quicklook import environment
from pyvista_quicklook import render
from pyvista_quicklook import warmup


def test_log_appends_only_when_enabled(tmp_path, monkeypatch):
    """Messages are written with a timestamp when logging is on, and dropped otherwise."""
    monkeypatch.setattr(config, 'APP_SUPPORT', tmp_path / 'support')
    monkeypatch.setattr(config, 'LOG_PATH', tmp_path / 'support' / 'pvql.log')
    environment.log({'log': False}, 'dropped')
    assert not config.LOG_PATH.exists()
    environment.log({'log': True}, 'first')
    environment.log({'log': True}, 'second')
    lines = config.LOG_PATH.read_text().splitlines()
    assert [line.split(' ', 2)[2] for line in lines] == ['first', 'second']


def test_log_never_raises(tmp_path, monkeypatch):
    """A log file that cannot be written is ignored."""
    blocker = tmp_path / 'file'
    blocker.write_text('x')
    monkeypatch.setattr(config, 'APP_SUPPORT', blocker / 'support')
    monkeypatch.setattr(config, 'LOG_PATH', blocker / 'support' / 'pvql.log')
    environment.log({'log': True}, 'lost')


def test_run_reports_a_timeout():
    """A command that outlives its timeout is reported with the task's name."""
    command = [sys.executable, '-c', 'import time; time.sleep(10)']
    with pytest.raises(environment.RenderError, match=r'Sleeping timed out after 0\.1 s'):
        environment.run({}, command, task='Sleeping', timeout=0.1)


def test_fill_rejects_an_empty_build(tmp_path):
    """A build that writes nothing is an error, and leaves nothing behind."""
    out = tmp_path / 'preview.png'
    with pytest.raises(environment.RenderError, match='Nothing was written'):
        cache.fill(out, {}, 'mesh.vtu', lambda scratch: scratch.touch())
    assert not list(tmp_path.iterdir())


def test_preview_needs_the_pyvista_command(tmp_path, monkeypatch):
    """A still drawn on a background needs the pyvista command beside the interpreter."""
    monkeypatch.setattr(config, 'CACHE_DIR', tmp_path / 'cache')
    monkeypatch.setattr(render.config_mod, 'resolve_pyvista', lambda cfg: None)
    source = tmp_path / 'mesh.vtu'
    source.write_text('x')
    with pytest.raises(environment.RenderError, match='No pyvista command-line interface'):
        render.preview(source, {**config.DEFAULTS, 'background': 'white'})


def test_warm_succeeds_without_a_writable_cache(tmp_path, monkeypatch):
    """Failing to record the warm-up does not fail the warm-up itself."""
    blocker = tmp_path / 'file'
    blocker.write_text('x')
    monkeypatch.setattr(config, 'CACHE_DIR', blocker / 'cache')
    monkeypatch.setattr(warmup.environment, 'interpreter', lambda cfg: sys.executable)
    monkeypatch.setattr(warmup.environment, 'run', lambda *a, **k: None)
    assert warmup.warm({}) >= 0


def test_what_a_successful_run_reports_is_logged(tmp_path, monkeypatch):
    """A run that succeeds but writes to stderr, as a fallback does, leaves that in the log."""
    monkeypatch.setattr(config, 'APP_SUPPORT', tmp_path / 'support')
    monkeypatch.setattr(config, 'LOG_PATH', tmp_path / 'support' / 'pvql.log')
    command = [sys.executable, '-c', 'import sys; sys.stderr.write("header skipped: X\\n")']
    environment.run({'log': True}, command, task='Reading the header')
    assert config.LOG_PATH.read_text().rstrip().endswith('Reading the header: header skipped: X')
