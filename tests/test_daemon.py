"""Tests for the render service loop and its request and reply files."""

from __future__ import annotations

import faulthandler
import json
import os
from pathlib import Path
import threading
import time
from types import SimpleNamespace

import pytest

from pyvista_quicklook import daemon
from pyvista_quicklook import environment
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
        # Lets the abandoned read finish, so its thread does not outlive the test.
        os.close(os.open(fifo, os.O_WRONLY | os.O_NONBLOCK))


def test_readable_works_off_the_main_thread(tmp_path):
    """Requests are answered on their own threads, so the guard cannot rely on signals."""
    sample = tmp_path / 'mesh.vtu'
    sample.write_bytes(b'x')
    answers = []
    worker = threading.Thread(target=lambda: answers.append(daemon.readable(str(sample))))
    worker.start()
    worker.join(10)
    assert answers == [True]


def test_handle_marks_a_file_that_holds_no_mesh(tmp_path, monkeypatch):
    """Only a file the exporter cannot read as a mesh is marked for showing as something else."""

    def unreadable(target, config, identity=None):
        message = 'Converting notes.dat failed.'
        raise environment.NotAMeshError(message)

    monkeypatch.setattr(daemon.config_mod, 'load', dict)
    monkeypatch.setattr(daemon, 'folder_is_reachable', lambda path: True)
    monkeypatch.setattr(daemon, 'produce', unreadable)
    request = tmp_path / 'token.pvqlreq'
    request.write_text(json.dumps({'path': str(tmp_path / 'notes.dat')}))
    daemon.handle(request)
    reply = json.loads((tmp_path / 'token.pvqlrep').read_text())
    assert reply == {'ok': False, 'error': 'Converting notes.dat failed.', 'not_a_mesh': True}


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
    monkeypatch.setattr(daemon.convert_mod, 'describe', lambda *args, **kwargs: {'type': 'X'})
    request = tmp_path / 'token.pvqlreq'
    request.write_text(json.dumps({'path': str(tmp_path / 'mesh.vtu')}))
    daemon.handle(request)
    reply = json.loads((tmp_path / 'token.pvqlrep').read_text())
    assert reply['ok'] is True
    assert Path(reply['png']).read_bytes() == b'png'
    assert reply['info'] == {'type': 'X'}


@pytest.mark.parametrize(
    ('cached', 'reachable', 'expected'),
    [
        ({'type': 'cached'}, True, {'type': 'cached'}),
        (None, True, {'type': 'header'}),
        ({'type': 'cached'}, False, None),
    ],
)
def test_a_description_alone_converts_nothing(tmp_path, monkeypatch, cached, reachable, expected):
    """A request for the description alone is answered from the cache or the header."""
    reads = []

    def describe(target, config, identity=None, *, read=True, timeout=None):
        reads.append(read)
        return cached

    monkeypatch.setattr(daemon.config_mod, 'load', dict)
    monkeypatch.setattr(daemon, 'folder_is_reachable', lambda path: reachable)
    monkeypatch.setattr(daemon, 'produce', lambda *args: pytest.fail('converted'))
    monkeypatch.setattr(daemon.convert_mod, 'describe', describe)
    monkeypatch.setattr(daemon.convert_mod, 'header', lambda source, config: {'type': 'header'})
    request = tmp_path / 'token.pvqlreq'
    request.write_text(
        json.dumps({'path': '/data/big.pv', 'describe': True, 'mtime': 1, 'size': 2})
    )
    daemon.handle(request)
    reply = json.loads((tmp_path / 'token.pvqlrep').read_text())
    assert reply == {'ok': True, 'info': expected}
    assert all(read is False for read in reads)


def test_a_still_image_is_sent_before_its_description(tmp_path, monkeypatch):
    """An image whose description is not cached is sent at once and described afterwards."""
    png = tmp_path / 'cached.png'
    png.write_bytes(b'png')
    reply = tmp_path / 'token.pvqlrep'
    replied_first = []

    def describe(target, config, identity=None, *, read=True, timeout=None):
        if read:
            replied_first.append(reply.exists())

    monkeypatch.setattr(daemon.config_mod, 'load', dict)
    monkeypatch.setattr(daemon, 'folder_is_reachable', lambda path: True)
    monkeypatch.setattr(daemon, 'produce', lambda target, config, identity: png)
    monkeypatch.setattr(daemon.convert_mod, 'describe', describe)
    monkeypatch.setattr(daemon.convert_mod, 'info_path', lambda identity: tmp_path / 'x.json')
    request = tmp_path / 'token.pvqlreq'
    request.write_text(json.dumps({'path': '/data/mesh.vtu', 'mtime': 1, 'size': 2}))
    daemon.handle(request)
    daemon._descriptions.join()
    assert 'info' not in json.loads(reply.read_text())
    assert replied_first == [True]


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


def test_serve_answers_each_request_on_its_own_thread(tmp_path, monkeypatch):
    """A request picked up while another is still being answered is not left waiting for it."""
    drop = tmp_path / 'container'
    drop.mkdir()
    monkeypatch.setattr(daemon, 'drop_dir', lambda: drop)
    monkeypatch.setattr(daemon.cache, 'discard_other_versions', lambda version: 0)
    monkeypatch.setattr(daemon.config_mod, 'load', lambda: {'warm_on_start': False})
    release, second = threading.Event(), threading.Event()
    released, finished = [], threading.Event()

    def handle(request):
        if request.stem == 'a':
            (drop / 'b.pvqlreq').write_text('{}')
            released.append(release.wait(5))
            finished.set()
        else:
            second.set()
            release.set()

    def sleep(seconds):
        if second.wait(seconds):
            raise StopLoopError

    monkeypatch.setattr(daemon, 'handle', handle)
    monkeypatch.setattr(daemon.time, 'sleep', sleep)
    (drop / 'a.pvqlreq').write_text('{}')
    with pytest.raises(StopLoopError):
        daemon.serve()
    release.set()
    assert second.is_set()
    finished.wait(5)
    assert released == [True], 'the second request waited for the first'


def test_serve_leaves_a_request_another_service_took(tmp_path, monkeypatch):
    """A request that is gone before it can be taken is skipped, not answered twice."""
    drop = tmp_path / 'container'
    drop.mkdir()
    monkeypatch.setattr(daemon, 'drop_dir', lambda: drop)
    monkeypatch.setattr(daemon.cache, 'discard_other_versions', lambda version: 0)
    monkeypatch.setattr(daemon.config_mod, 'load', lambda: {'warm_on_start': False})
    monkeypatch.setattr(daemon, 'handle', lambda request: pytest.fail('handled'))

    def gone(self, target):
        raise FileNotFoundError(self)

    def stop(seconds):
        raise StopLoopError

    monkeypatch.setattr(Path, 'rename', gone)
    monkeypatch.setattr(daemon.time, 'sleep', stop)
    (drop / 'a.pvqlreq').write_text('{}')
    with pytest.raises(StopLoopError):
        daemon.serve()


class StopLoopError(Exception):
    """Raised to end the service loop in a test."""


def test_serve_answers_requests_and_sweeps(tmp_path, monkeypatch):
    """The loop prepares its folder, warms up, answers each request, and sweeps each minute."""
    drop = tmp_path / 'container'
    handled, swept, warmed = [], [], []
    monkeypatch.setattr(daemon, 'drop_dir', lambda: drop)
    monkeypatch.setattr(daemon.cache, 'discard_other_versions', lambda version: 0)
    monkeypatch.setattr(daemon.config_mod, 'load', lambda: {'warm_on_start': True})
    monkeypatch.setattr(daemon, 'warm_in_background', warmed.append)
    monkeypatch.setattr(
        daemon, 'handle', lambda request: handled.append(request.name) or request.unlink()
    )
    monkeypatch.setattr(daemon, 'sweep', swept.append)
    monkeypatch.setattr(
        daemon.threading,
        'Thread',
        lambda target, args, daemon: SimpleNamespace(start=lambda: target(*args)),
    )
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
    assert handled == ['a.pvqltaken', 'b.pvqltaken']
    assert list(drop.iterdir()) == []
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


def test_sweep_removes_staged_copies_left_behind(tmp_path):
    """A staging folder older than the cutoff goes, with whatever copy it holds."""
    old = tmp_path / 'abc.staging'
    old.mkdir()
    (old / 'mesh.vtu').write_bytes(b'x')
    stamp = time.time() - daemon.STALE_SECONDS - 10
    os.utime(old / 'mesh.vtu', (stamp, stamp))
    os.utime(old, (stamp, stamp))
    fresh = tmp_path / 'def.staging'
    fresh.mkdir()
    daemon.sweep(tmp_path)
    assert not old.exists()
    assert fresh.exists()


def test_a_copy_is_described_in_the_time_left(tmp_path, monkeypatch):
    """A staged copy goes once the reply is read, so it is described before, within the wait."""
    png = tmp_path / 'cached.png'
    png.write_bytes(b'png')
    reads = []

    def describe(target, config, identity=None, *, read=True, timeout=None):
        reads.append((target, read, timeout))
        return {'type': 'X'} if read else None

    monkeypatch.setattr(daemon.config_mod, 'load', dict)
    monkeypatch.setattr(daemon, 'folder_is_reachable', lambda path: False)
    monkeypatch.setattr(daemon, 'produce', lambda target, config, identity: png)
    monkeypatch.setattr(daemon.convert_mod, 'describe', describe)
    request = tmp_path / 'token.pvqlreq'
    request.write_text(json.dumps({'path': '/private/mesh.vtu', 'copy': '/staged/mesh.vtu'}))
    daemon.handle(request)
    assert json.loads((tmp_path / 'token.pvqlrep').read_text())['info'] == {'type': 'X'}
    [cached, read] = reads
    assert cached == ('/staged/mesh.vtu', False, None)
    assert read[:2] == ('/staged/mesh.vtu', True)
    assert 1 < read[2] <= daemon.reply_seconds({}) - daemon.REPLY_MARGIN_SECONDS


def test_a_copy_is_not_described_once_the_wait_is_over(tmp_path, monkeypatch):
    """A request that has waited out the extension's patience is answered without a panel."""
    png = tmp_path / 'cached.png'
    png.write_bytes(b'png')
    reads = []
    monkeypatch.setattr(daemon.config_mod, 'load', dict)
    monkeypatch.setattr(daemon, 'folder_is_reachable', lambda path: False)
    monkeypatch.setattr(daemon, 'produce', lambda target, config, identity: png)
    monkeypatch.setattr(
        daemon.convert_mod, 'describe', lambda *args, read=True, **kwargs: reads.append(read)
    )
    request = tmp_path / 'token.pvqlreq'
    request.write_text(json.dumps({'path': '/private/mesh.vtu', 'copy': '/staged/mesh.vtu'}))
    stamp = time.time() - daemon.reply_seconds({})
    os.utime(request, (stamp, stamp))
    daemon.handle(request)
    assert 'info' not in json.loads((tmp_path / 'token.pvqlrep').read_text())
    assert reads == [False]


def test_files_are_described_one_at_a_time(tmp_path, monkeypatch):
    """Queued descriptions run one after another, each file once, and only when wanted."""
    started, release = threading.Event(), threading.Event()
    calls = []

    def describe(target, config, identity=None, **kwargs):
        calls.append(target)
        started.set()
        release.wait(5)

    monkeypatch.setattr(daemon.convert_mod, 'describe', describe)
    monkeypatch.setattr(daemon.convert_mod, 'info_path', lambda identity: tmp_path / identity[0])
    first, second = ('a.json', 1, 2), ('b.json', 1, 2)
    assert daemon.describe_later('/data/a.vtu', {}, first)
    assert started.wait(5)
    assert not daemon.describe_later('/data/a.vtu', {}, first)
    assert daemon.describe_later('/data/b.vtu', {}, second)
    assert not daemon.describe_later('/data/c.vtu', {'info_panel': 'hidden'}, ('c.json', 1, 2))
    assert not daemon.describe_later('/data/d.vtu', {}, None)
    assert calls == ['/data/a.vtu']
    release.set()
    daemon._descriptions.join()
    assert calls == ['/data/a.vtu', '/data/b.vtu']
    (tmp_path / 'a.json').write_text('{}')
    assert not daemon.describe_later('/data/a.vtu', {}, first)


def test_a_description_that_raises_does_not_stop_the_others(tmp_path, monkeypatch):
    """An unexpected error describing one file is logged, and the next is still described."""
    calls = []

    def describe(target, config, identity=None, **kwargs):
        calls.append(target)
        if target == '/data/a.vtu':
            raise ZeroDivisionError

    monkeypatch.setattr(daemon.convert_mod, 'describe', describe)
    monkeypatch.setattr(daemon.convert_mod, 'info_path', lambda identity: tmp_path / identity[0])
    assert daemon.describe_later('/data/a.vtu', {}, ('a.json', 1, 2))
    assert daemon.describe_later('/data/b.vtu', {}, ('b.json', 1, 2))
    daemon._descriptions.join()
    assert calls == ['/data/a.vtu', '/data/b.vtu']
