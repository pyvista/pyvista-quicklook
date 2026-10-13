"""Fixtures shared by the test modules."""

from __future__ import annotations

from pathlib import Path
import subprocess

import pytest

from pyvista_quicklook import cli
from pyvista_quicklook import config


@pytest.fixture(autouse=True)
def private_home(tmp_path_factory):
    """Give every test a home folder of its own, and return it.

    Locations named when the package is imported are moved there too. Patched on its
    own, so a test that undoes its own patches does not undo these.
    """
    home = tmp_path_factory.mktemp('home')
    library = home / 'Library'
    support = library / 'Application Support' / 'PyVistaQuickLook'
    container = library / 'Containers' / config.EXT_BUNDLE_ID / 'Data' / 'tmp'
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(Path, 'home', classmethod(lambda cls: home))
        patch.setattr(config, 'APP_SUPPORT', support)
        patch.setattr(config, 'CONFIG_PATH', support / 'config.json')
        patch.setattr(config, 'MIRROR_PATH', container / 'config.json')
        patch.setattr(config, 'LOG_PATH', support / 'pvql.log')
        patch.setattr(config, 'CACHE_DIR', library / 'Caches' / 'PyVistaQuickLook')
        patch.setattr(cli, 'APP_DIRS', (home / 'Applications',))
        patch.setattr(cli, 'SERVICE_LOG', library / 'Logs' / 'pvqld.log')
        yield home


@pytest.fixture
def home(tmp_path, monkeypatch):
    """Point every location the helper reads or writes at tmp_path, and return it."""
    support = tmp_path / 'support'
    monkeypatch.setattr(config, 'APP_SUPPORT', support)
    monkeypatch.setattr(config, 'CONFIG_PATH', support / 'config.json')
    monkeypatch.setattr(config, 'MIRROR_PATH', tmp_path / 'container' / 'config.json')
    monkeypatch.setattr(config, 'LOG_PATH', support / 'pvql.log')
    monkeypatch.setattr(config, 'CACHE_DIR', tmp_path / 'cache')
    monkeypatch.setattr(cli, 'APP_DIRS', (tmp_path / 'Applications',))
    monkeypatch.setattr(cli, 'SERVICE_LOG', tmp_path / 'pvqld.log')
    monkeypatch.setattr(cli.daemon_mod, 'agent_path', lambda: tmp_path / 'agent.plist')
    monkeypatch.setattr(cli.daemon_mod, 'drop_dir', lambda: tmp_path / 'container')
    monkeypatch.setattr(cli.daemon_mod, 'legacy_agent_path', lambda: tmp_path / 'old-agent.plist')
    monkeypatch.setattr(
        cli.daemon_mod, 'legacy_drop_dir', lambda: tmp_path / 'old-container' / 'Data' / 'tmp'
    )
    return tmp_path


@pytest.fixture
def commands(monkeypatch):
    """Record every system command the CLI runs, and answer each with success."""
    ran = []
    monkeypatch.setattr(
        cli.subprocess,
        'run',
        lambda command, **k: (
            ran.append(command) or subprocess.CompletedProcess(command, 0, '', '')
        ),
    )
    return ran
