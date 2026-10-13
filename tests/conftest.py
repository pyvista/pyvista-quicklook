"""Fixtures and helpers shared by the test modules."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import time

import pytest

from pyvista_quicklook import cli
from pyvista_quicklook import config
from pyvista_quicklook import daemon

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / 'build'


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
    monkeypatch.setattr(cli.daemon_mod, 'unbundled_agent_path', lambda: tmp_path / 'agent.plist')
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


def request(folder, **payload):
    """Write a request in ``folder``, have the service handle it, and return the parsed reply."""
    asked = folder / f'token{daemon.REQUEST_SUFFIX}'
    asked.write_text(json.dumps(payload))
    daemon.handle(asked)
    return json.loads((folder / f'token{daemon.REPLY_SUFFIX}').read_text())


def reachable(monkeypatch, value):
    """Make the service find every file's folder reachable, or none."""
    monkeypatch.setattr(daemon, 'folder_is_reachable', lambda path: value)


def settings(monkeypatch, values):
    """Make the service load the given settings instead of the configuration file."""
    monkeypatch.setattr(daemon.config_mod, 'load', lambda: dict(values))


def wait_for(condition, timeout=5):
    """Poll until ``condition`` holds, failing the test when it does not within ``timeout``."""
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            pytest.fail('timed out waiting for a condition')
        time.sleep(0.01)


def compiled(name, sources, flags=(), main=None):
    """Return a Swift tool built from ``sources`` under build/, recompiled only when stale.

    ``main`` is the text of a main.swift to compile with the sources; it is kept under
    build/ as well, in a folder of its own since swiftc wants that name, and rewritten
    only when it changes, so it dates the tool like a file. Paths among ``flags`` count
    as sources too.
    """
    tool = BUILD / name
    BUILD.mkdir(parents=True, exist_ok=True)
    sources = [Path(source) for source in sources]
    if main is not None:
        entry = BUILD / f'{name}.main' / 'main.swift'
        entry.parent.mkdir(exist_ok=True)
        if not entry.exists() or entry.read_text() != main:
            entry.write_text(main)
        sources.append(entry)
    dated = sources + [flag for flag in flags if isinstance(flag, Path)]
    if tool.exists() and tool.stat().st_mtime >= max(path.stat().st_mtime for path in dated):
        return tool
    subprocess.run(
        ['swiftc', *map(str, flags), *map(str, sources), '-o', str(tool)],
        check=True,
        capture_output=True,
        text=True,
    )
    return tool
