"""Tests for preloading the PyVista environment and remembering when it was done."""

from __future__ import annotations

import sys

import pytest

from pyvista_quicklook import config
from pyvista_quicklook import environment
from pyvista_quicklook import preload


def test_preload_script_ships_with_the_package():
    """The preload script the PyVista interpreter runs is part of the package."""
    assert preload.SCRIPT.is_file()


def test_preload_reports_a_missing_interpreter(monkeypatch):
    """Preloading without a usable PyVista environment explains what to set."""
    monkeypatch.setattr(preload.config_mod, 'find_python', lambda configured=None: None)
    with pytest.raises(environment.RenderError, match='interpreter'):
        preload.run({})


def test_preload_succeeds_without_a_writable_cache(tmp_path, monkeypatch):
    """Failing to record the preload does not fail the preload itself."""
    blocker = tmp_path / 'file'
    blocker.write_text('x')
    monkeypatch.setattr(config, 'CACHE_DIR', blocker / 'cache')
    monkeypatch.setattr(preload.environment, 'interpreter', lambda cfg: sys.executable)
    monkeypatch.setattr(preload.environment, 'run', lambda *a, **k: None)
    assert preload.run({}) >= 0


def test_preloaded_recently_reads_the_stamp(tmp_path, monkeypatch):
    """A fresh stamp counts as preload; a missing one does not."""
    monkeypatch.setattr(preload.config_mod, 'CACHE_DIR', tmp_path)
    assert preload.preloaded_recently() is False
    preload.stamp_path().touch()
    assert preload.preloaded_recently() is True
    assert preload.preloaded_recently(within=0) is False
