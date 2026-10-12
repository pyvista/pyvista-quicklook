"""Tests for the render service loop and its request and reply files."""

from __future__ import annotations

import faulthandler
import json
import os
from pathlib import Path
import threading
import time

import pytest

from pyvista_quicklook import daemon
from pyvista_quicklook.environment import RenderError


def test_exchange_locations_are_in_the_extension_containers():
    """The service and the extension exchange files inside the extension's container."""
    assert daemon.EXT_BUNDLE_ID in daemon.drop_dir().parts
    assert daemon.LEGACY_EXT_BUNDLE_ID in daemon.legacy_drop_dir().parts
    assert daemon.agent_path().name == f'{daemon.LABEL}.plist'
    assert daemon.legacy_agent_path().name == f'{daemon.LEGACY_LABEL}.plist'


def test_folder_is_reachable_remembers_its_answer(tmp_path, monkeypatch):
    """A folder is probed once, and the answer is reused for its other files for a while."""
    probes = []
    monkeypatch.setattr(daemon, '_reachable_folders', {})
    monkeypatch.setattr(daemon, 'readable', lambda path: probes.append(path) or True)
    assert daemon.folder_is_reachable(str(tmp_path / 'a.vtu'))
    assert daemon.folder_is_reachable(str(tmp_path / 'b.vtu'))
    assert probes == [str(tmp_path / 'a.vtu')]
    later = time.monotonic() + daemon.FOLDER_MEMO_SECONDS + 1
    monkeypatch.setattr(daemon.time, 'monotonic', lambda: later)
    assert daemon.folder_is_reachable(str(tmp_path / 'c.vtu'))
    assert probes == [str(tmp_path / 'a.vtu'), str(tmp_path / 'c.vtu')]


def test_readable_gives_up_on_a_file_that_blocks(tmp_path):
    """Opening a file that never answers is abandoned when the timer fires."""
    fifo = tmp_path / 'blocks'
    os.mkfifo(fifo)
    # Ends the run with a traceback rather than hanging if the timer never fires.
    faulthandler.dump_traceback_later(10, exit=True)
    try:
        assert daemon.readable(str(fifo), seconds=0.1) is False
    finally:
        faulthandler.cancel_dump_traceback_later()


def test_handle_reports_an_unexpected_exception(tmp_path, monkeypatch):
    """An error that is not a RenderError is reported with its type, not raised."""

    def explode(target, config, identity=None):
        message = 'boom'
        raise ValueError(message)

    monkeypatch.setattr(daemon.config_mod, 'load', dict)
    monkeypatch.setattr(daemon, 'folder_is_reachable', lambda path: True)
    monkeypatch.setattr(daemon, 'produce', explode)
    request = tmp_path / 'token.pvqlreq'
    request.write_text(json.dumps({'path': str(tmp_path / 'mesh.vtu')}))
    daemon.handle(request)
    reply = json.loads((tmp_path / 'token.pvqlrep').read_text())
    assert reply == {'ok': False, 'error': 'ValueError: boom'}
    assert not request.exists()


def test_handle_delivers_a_still_image(tmp_path, monkeypatch):
    """A still image is copied beside the reply and named under ``png``."""
    png = tmp_path / 'cached.png'
    png.write_bytes(b'png')
    monkeypatch.setattr(daemon.config_mod, 'load', dict)
    monkeypatch.setattr(daemon, 'folder_is_reachable', lambda path: True)
    monkeypatch.setattr(daemon, 'produce', lambda target, config, identity: png)
    monkeypatch.setattr(
        daemon.convert_mod, 'describe', lambda target, built, config: {'type': 'X'}
    )
    request = tmp_path / 'token.pvqlreq'
    request.write_text(json.dumps({'path': str(tmp_path / 'mesh.vtu')}))
    daemon.handle(request)
    reply = json.loads((tmp_path / 'token.pvqlrep').read_text())
    assert reply['ok'] is True
    assert Path(reply['png']).read_bytes() == b'png'
    assert reply['info'] == {'type': 'X'}


def test_handle_leaves_out_a_description_it_cannot_get(tmp_path, monkeypatch):
    """A preview whose file cannot be described is delivered without info."""
    png = tmp_path / 'cached.png'
    png.write_bytes(b'png')
    monkeypatch.setattr(daemon.config_mod, 'load', dict)
    monkeypatch.setattr(daemon, 'folder_is_reachable', lambda path: True)
    monkeypatch.setattr(daemon, 'produce', lambda target, config, identity: png)
    monkeypatch.setattr(daemon.convert_mod, 'describe', lambda target, built, config: None)
    request = tmp_path / 'token.pvqlreq'
    request.write_text(json.dumps({'path': str(tmp_path / 'mesh.vtu')}))
    daemon.handle(request)
    reply = json.loads((tmp_path / 'token.pvqlrep').read_text())
    assert reply['ok'] is True
    assert 'info' not in reply


def test_sweep_removes_only_stale_replies(tmp_path):
    """Uncollected replies older than the cutoff go; fresh ones and requests stay."""
    old = time.time() - daemon.STALE_SECONDS - 10
    stale = [tmp_path / name for name in ('a.pvqlrep', 'a.png', 'a.ply')]
    fresh = [tmp_path / name for name in ('b.pvqlrep', 'b.png', 'b.ply', 'c.pvqlreq')]
    for path in stale + fresh:
        path.write_text('x')
    for path in stale:
        os.utime(path, (old, old))
    daemon.sweep(tmp_path)
    assert not any(path.exists() for path in stale)
    assert all(path.exists() for path in fresh)


def test_sweep_ignores_a_reply_that_vanishes(tmp_path, monkeypatch):
    """A reply collected while sweeping is not an error."""
    (tmp_path / 'a.pvqlrep').write_text('x')

    def vanished(self, *args, **kwargs):
        raise FileNotFoundError

    monkeypatch.setattr(Path, 'stat', vanished)
    daemon.sweep(tmp_path)


class StopLoopError(Exception):
    """Raised to end the service loop in a test."""


def test_serve_answers_requests_and_sweeps(tmp_path, monkeypatch):
    """The loop prepares its folder, warms up, answers each request, and sweeps each minute."""
    drop = tmp_path / 'container'
    handled, swept, warmed = [], [], []
    monkeypatch.setattr(daemon, 'drop_dir', lambda: drop)
    monkeypatch.setattr(daemon.cache, 'discard_other_scenes', lambda version: 0)
    monkeypatch.setattr(daemon.config_mod, 'load', lambda: {'warm_on_start': True})
    monkeypatch.setattr(daemon, 'warm_in_background', warmed.append)
    monkeypatch.setattr(
        daemon, 'handle', lambda request: handled.append(request.name) or request.unlink()
    )
    monkeypatch.setattr(daemon, 'sweep', swept.append)
    # Started at 0, then polls at 30 (not due), 100 (due), and 130 (not due again).
    clock = iter([0.0, 30.0, 100.0, 100.0, 130.0])
    monkeypatch.setattr(daemon.time, 'monotonic', lambda: next(clock))
    polls = []

    def sleep(seconds):
        polls.append(seconds)
        if len(polls) == 3:
            raise StopLoopError

    monkeypatch.setattr(daemon.time, 'sleep', sleep)
    drop.mkdir()
    (drop / 'b.pvqlreq').write_text('{}')
    (drop / 'a.pvqlreq').write_text('{}')
    with pytest.raises(StopLoopError):
        daemon.serve()
    assert handled == ['a.pvqlreq', 'b.pvqlreq']
    assert swept == [drop]
    assert warmed == [{'warm_on_start': True}]


@pytest.fixture
def drop(tmp_path, monkeypatch):
    """Exchange files in tmp_path and return it."""
    folder = tmp_path / 'container'
    monkeypatch.setattr(daemon, 'drop_dir', lambda: folder)
    return folder


def answer(drop, payload, result=None):
    """Answer the next request in ``drop`` from another thread, as the service would."""

    def run():
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            requests = list(drop.glob(f'*{daemon.REQUEST_SUFFIX}'))
            if requests:
                request = json.loads(requests[0].read_text())
                if result is not None:
                    result.append(request)
                requests[0].unlink()
                daemon.write_json(requests[0].with_suffix(daemon.REPLY_SUFFIX), payload)
                return
            time.sleep(0.01)

    thread = threading.Thread(target=run)
    thread.start()
    return thread


def test_request_preview_returns_the_delivered_scene(tmp_path, drop):
    """A request names the resolved file and its identity, and the reply's scene is returned."""
    source = tmp_path / 'mesh.vtu'
    source.write_text('x')
    seen = []
    thread = answer(drop, {'ok': True, 'scene': '/out/scene.ply'}, seen)
    assert daemon.request_preview(str(source), timeout=10) == Path('/out/scene.ply')
    thread.join()
    assert seen[0]['path'] == str(source.resolve())
    assert seen[0]['size'] == 1
    assert seen[0]['mtime'] == int(source.stat().st_mtime)
    assert not list(drop.iterdir())


def test_request_preview_raises_the_reported_error(tmp_path, drop):
    """A failed reply is raised with the service's message."""
    source = tmp_path / 'mesh.vtu'
    source.write_text('x')
    thread = answer(drop, {'ok': False, 'error': 'cannot read'})
    with pytest.raises(RenderError, match='cannot read'):
        daemon.request_preview(str(source), timeout=10)
    thread.join()


def test_request_preview_times_out_without_a_service(tmp_path, drop):
    """With nothing answering, the caller is told how to check the service."""
    source = tmp_path / 'mesh.vtu'
    source.write_text('x')
    with pytest.raises(RenderError, match='did not answer'):
        daemon.request_preview(str(source), timeout=0)
