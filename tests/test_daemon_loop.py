"""Tests for the render service loop, its guards and queues, and the memory budget."""

from __future__ import annotations

import errno
import faulthandler
import json
import os
from pathlib import Path
import sys
import threading
import time
from types import SimpleNamespace

from conftest import wait_for
import pytest

from pyvista_quicklook import daemon
from pyvista_quicklook import plist
from pyvista_quicklook.environment import RenderError


class StopLoopError(Exception):
    """Raised to end the service loop in a test."""


def test_exchange_locations_are_in_the_extension_containers():
    """The service and the extension exchange files inside the extension's container."""
    assert daemon.drop_dir() == daemon.config_mod.MIRROR_PATH.parent
    assert daemon.config_mod.EXT_BUNDLE_ID in daemon.drop_dir().parts
    assert daemon.LEGACY_EXT_BUNDLE_ID in daemon.legacy_drop_dir().parts
    assert daemon.unbundled_agent_path().name == f'{daemon.UNBUNDLED_LABEL}.plist'
    assert daemon.legacy_agent_path().name == f'{daemon.LEGACY_LABEL}.plist'


def test_bundled_agent_runs_the_launcher_and_keeps_it_running():
    """The agent the app carries runs the bundled launcher and keeps the service up."""
    agent = daemon.bundled_agent_plist()
    assert agent['BundleProgram'] == f'Contents/MacOS/{plist.SERVICE_EXECUTABLE}'
    assert agent['KeepAlive'] is True
    assert agent['Label'] == daemon.LABEL


def test_a_missing_file_says_nothing_about_its_folder(tmp_path, monkeypatch):
    """A file's own trouble is not the folder's, so the folder is not remembered as unreachable."""
    monkeypatch.setattr(daemon, '_reachable_folders', {})
    assert daemon.readable(str(tmp_path / 'nope')) is None
    assert daemon.folder_is_reachable(str(tmp_path / 'nope')) is True
    assert daemon._reachable_folders == {}
    sample = tmp_path / 'mesh.vtu'
    sample.write_text('data')
    assert daemon.folder_is_reachable(str(sample)) is True
    assert daemon._reachable_folders[str(tmp_path)][1] is True


def test_a_private_folder_is_unreachable_and_remembered(tmp_path, monkeypatch):
    """MacOS's refusal to open a file in a protected folder marks the folder unreachable."""
    monkeypatch.setattr(daemon, '_reachable_folders', {})

    def refused(*args, **kwargs):
        raise PermissionError(errno.EPERM, 'Operation not permitted')

    monkeypatch.setattr(daemon, 'open', refused, raising=False)
    sample = tmp_path / 'mesh.vtu'
    assert daemon.readable(str(sample)) is False
    assert daemon.folder_is_reachable(str(sample)) is False
    assert daemon._reachable_folders[str(tmp_path)][1] is False


def test_readable_accepts_an_ordinary_file(tmp_path):
    """A file the service can open is reported readable."""
    sample = tmp_path / 'mesh.vtu'
    sample.write_text('data')
    assert daemon.readable(str(sample)) is True


def test_readable_accepts_a_folder(tmp_path):
    """A folder that can be listed is readable, which is how a DICOM series arrives."""
    series = tmp_path / 'series'
    series.mkdir()
    assert daemon.readable(str(series)) is True


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
    """A reply collected while sweeping is not an error, and nothing else is touched."""
    (tmp_path / 'a.pvqlrep').write_text('x')

    def vanished(self, *args, **kwargs):
        raise FileNotFoundError

    monkeypatch.setattr(Path, 'stat', vanished)
    daemon.sweep(tmp_path)
    assert os.listdir(tmp_path) == ['a.pvqlrep']


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


def test_the_sweep_leaves_a_copy_a_request_still_reads(tmp_path):
    """A staged copy older than the cutoff stays while its request is being handled."""
    folder = tmp_path / 'abc.staging'
    folder.mkdir()
    (folder / 'mesh.vtu').write_bytes(b'x')
    stamp = time.time() - daemon.STALE_SECONDS - 10
    os.utime(folder, (stamp, stamp))
    with daemon.staged_in_use(str(folder / 'mesh.vtu')):
        daemon.sweep(tmp_path)
        assert folder.exists()
    with daemon.staged_in_use(None):
        daemon.sweep(tmp_path)
    assert not folder.exists()


def test_serve_answers_each_request_on_its_own_thread(tmp_path, monkeypatch):
    """A request picked up while another is still being answered is not left waiting for it."""
    drop = tmp_path / 'container'
    drop.mkdir()
    monkeypatch.setattr(daemon, 'drop_dir', lambda: drop)
    monkeypatch.setattr(daemon.cache, 'discard_other_versions', lambda version: 0)
    monkeypatch.setattr(daemon, 'prune_in_background', lambda: None)
    monkeypatch.setattr(daemon.config_mod, 'load', lambda: {'preload_on_start': False})
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
    monkeypatch.setattr(daemon, 'prune_in_background', lambda: None)
    monkeypatch.setattr(daemon.config_mod, 'load', lambda: {'preload_on_start': False})
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


def test_serve_answers_requests_and_sweeps(tmp_path, monkeypatch):
    """The loop prepares its folder, preloads, prunes, answers requests, mirrors, and sweeps."""
    drop = tmp_path / 'container'
    handled, swept, preloaded, pruning = [], [], [], []
    monkeypatch.setattr(daemon, 'drop_dir', lambda: drop)
    monkeypatch.setattr(daemon.cache, 'discard_other_versions', lambda version: 0)
    monkeypatch.setattr(daemon, 'prune_in_background', lambda: pruning.append(True))
    monkeypatch.setattr(daemon.config_mod, 'load', lambda: {'preload_on_start': True})
    monkeypatch.setattr(daemon.config_mod, 'mirror', lambda: preloaded.append('mirrored'))
    monkeypatch.setattr(daemon, 'preload_in_background', preloaded.append)
    monkeypatch.setattr(
        daemon, 'handle', lambda request: handled.append(request.name) or request.unlink()
    )
    monkeypatch.setattr(daemon, 'sweep', swept.append)
    monkeypatch.setattr(
        daemon.threading,
        'Thread',
        lambda target, args, daemon: SimpleNamespace(start=lambda: target(*args)),
    )
    # Started at 0; polls at 0.5 (nothing due), 70 (a mirror and a sweep due), 70.5 (neither).
    clock = iter([0.0, 0.5, 0.5, 70.0, 70.0, 70.0, 70.0, 70.5, 70.5])
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
    # Settings are mirrored once a second has passed, not on every poll.
    assert preloaded == [{'preload_on_start': True}, 'mirrored']
    assert pruning == [True]


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
    assert 'scene' not in seen[0]
    assert not list(drop.iterdir())


def test_request_preview_can_ask_for_the_picture(tmp_path, drop):
    """A request for the picture says so, whatever previews start as."""
    source = tmp_path / 'mesh.vtu'
    source.write_text('x')
    seen = []
    thread = answer(drop, {'ok': True, 'png': '/out/still.png'}, seen)
    assert daemon.request_preview(str(source), timeout=10, scene=False) == Path('/out/still.png')
    thread.join()
    assert seen[0]['scene'] is False


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


def test_the_budget_is_half_the_memory_unless_set_and_zero_lifts_it():
    """Unset, the budget is half the Mac's memory; a size is in megabytes; 0 means no limit."""
    assert daemon.memory_budget({}) == daemon.physical_memory() // 2
    assert daemon.memory_budget({'max_conversion_memory_mb': 0}) == sys.maxsize
    assert daemon.memory_budget({'max_conversion_memory_mb': 512}) == 512_000_000


def test_a_conversion_is_estimated_from_the_file_size():
    """A conversion is expected to need the interpreter plus a multiple of the file's bytes."""
    assert daemon.conversion_memory('/data/a.vtu', ('/data/a.vtu', 1, 10)) == (
        daemon.BASE_MEMORY + 10 * daemon.MEMORY_PER_FILE_BYTE
    )


def test_a_folder_or_a_file_of_unknown_size_converts_alone(tmp_path):
    """With no size to go by, a conversion is given the whole budget to itself."""
    assert daemon.conversion_memory(str(tmp_path), (str(tmp_path), 1, 10)) == sys.maxsize
    assert daemon.conversion_memory('/data/a.vtu', None) == sys.maxsize


def test_a_big_conversion_waits_its_turn_rather_than_forever():
    """Waiters start in order, so a conversion too big to share is not passed over."""
    budget = daemon.MemoryBudget()
    order = []
    release = threading.Event()
    started = threading.Event()

    def run(name, amount, wait=False):
        with budget.reserve(amount, 10):
            order.append(name)
            started.set()
            if wait:
                release.wait(5)

    first = threading.Thread(target=run, args=('small', 1, True), daemon=True)
    first.start()
    assert started.wait(5)
    big = threading.Thread(target=run, args=('big', 100), daemon=True)
    big.start()
    wait_for(lambda: len(budget.waiting) == 1)
    later = threading.Thread(target=run, args=('later', 1), daemon=True)
    later.start()
    wait_for(lambda: len(budget.waiting) == 2)
    assert order == ['small']
    release.set()
    for thread in (first, big, later):
        thread.join(5)
    assert order == ['small', 'big', 'later']


def test_background_work_waits_behind_the_reader(tmp_path):
    """A preview the reader asked for starts before background work that asked earlier."""
    budget = daemon.MemoryBudget()
    order = []

    def wait(name, background):
        with budget.reserve(1, 1, background=background):
            order.append(name)

    with budget.reserve(1, 1):
        later = threading.Thread(target=wait, args=('description', True))
        later.start()
        wait_for(lambda: len(budget.waiting) == 1)
        sooner = threading.Thread(target=wait, args=('preview', False))
        sooner.start()
        wait_for(lambda: len(budget.waiting) == 2)
    later.join(5)
    sooner.join(5)
    assert order == ['preview', 'description']


def test_preload_in_background_survives_a_failing_preload(monkeypatch):
    """A failing preload never brings the service down, and is logged."""
    calls, logged = [], []

    def explode(config):
        calls.append(config)
        message = 'no interpreter'
        raise RenderError(message)

    monkeypatch.setattr(daemon.preload_mod, 'preloaded_recently', lambda: False)
    monkeypatch.setattr(daemon.preload_mod, 'run', explode)
    monkeypatch.setattr(daemon.environment, 'log', lambda config, text: logged.append(text))
    thread = daemon.preload_in_background({'log': True})
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert calls == [{'log': True}]
    assert logged == ['preload failed: no interpreter']


def test_preload_in_background_skips_a_recent_preload(monkeypatch):
    """The service does not compete with a preload the installer just ran."""
    called = []
    monkeypatch.setattr(daemon.preload_mod, 'preloaded_recently', lambda: True)
    monkeypatch.setattr(daemon.preload_mod, 'run', lambda config: called.append(config))
    daemon.preload_in_background({}).join(timeout=5)
    assert called == []


@pytest.mark.parametrize(
    ('minutes', 'last', 'now', 'due'),
    [
        (60, None, 0.0, True),
        (60, 0.0, 3599.0, False),
        (60, 0.0, 3600.0, True),
        (0, None, 0.0, False),
    ],
)
def test_prune_is_due_on_the_configured_interval(minutes, last, now, due):
    """The service checks at start and then every interval, and never when it is 0."""
    assert daemon.prune_due(minutes, last, now) is due


@pytest.mark.parametrize(
    ('value', 'minutes'),
    [
        (15, 15),
        ('30', 30),
        (0, 0),
        (-5, 0),
        (0.001, 1),
        (None, 1440),
        (True, 1440),
        ('often', 1440),
        ([1], 1440),
        (float('inf'), 1440),
    ],
)
def test_a_hand_edited_interval_is_read_or_defaulted(value, minutes):
    """A value written by hand is read when it can be, defaulted when not, and floored."""
    assert daemon.check_minutes({'cache_check_minutes': value}) == minutes


def test_pruner_rereads_its_interval_and_checks_when_due(monkeypatch):
    """The pruner checks at start, then once the interval is up, rereading it each time."""
    settings = iter([{'cache_check_minutes': 2}] * 3 + [{'cache_check_minutes': 0}])
    monkeypatch.setattr(daemon.config_mod, 'load', lambda: next(settings))
    pruned = []
    monkeypatch.setattr(daemon.cache, 'prune', lambda config, may_look: pruned.append(config))
    # Under the first interval the last tick would be due, so only a reread 0 skips it.
    clock = iter([0.0, 0.0, 60.0, 120.0, 120.0, 240.0])
    monkeypatch.setattr(daemon.time, 'time', lambda: next(clock))
    polls = []

    def sleep(seconds):
        polls.append(seconds)
        if len(polls) == 4:
            raise StopLoopError

    monkeypatch.setattr(daemon.time, 'sleep', sleep)
    with pytest.raises(StopLoopError):
        daemon.prune_periodically()
    assert len(pruned) == 2
    # It waits no longer than the interval, so a short one is honoured.
    assert polls == [120, 120, 120, daemon.PRUNE_POLL_SECONDS]


def test_a_failed_prune_is_logged_and_the_pruner_goes_on(monkeypatch):
    """An error in one check is logged, and the next check still happens."""
    monkeypatch.setattr(daemon.config_mod, 'load', lambda: {'cache_check_minutes': 1})

    def broken(config, may_look):
        message = 'disk on fire'
        raise RuntimeError(message)

    monkeypatch.setattr(daemon.cache, 'prune', broken)
    logged = []
    monkeypatch.setattr(daemon.environment, 'log', lambda config, text: logged.append(text))
    clock = iter([0.0, 0.0, 60.0, 60.0])
    monkeypatch.setattr(daemon.time, 'time', lambda: next(clock))
    polls = []

    def sleep(seconds):
        polls.append(seconds)
        if len(polls) == 2:
            raise StopLoopError

    monkeypatch.setattr(daemon.time, 'sleep', sleep)
    with pytest.raises(StopLoopError):
        daemon.prune_periodically()
    assert logged == ['prune failed: RuntimeError: disk on fire'] * 2


def test_the_pruner_looks_in_private_folders_only_once_a_preview_has(tmp_path, monkeypatch):
    """Private folders and other volumes wait until a preview has read somewhere in them."""
    monkeypatch.setattr(daemon.Path, 'home', lambda: tmp_path)
    monkeypatch.setattr(daemon, '_reachable_folders', {})
    desktop = tmp_path / 'Desktop' / 'unzipped' / 'mesh.vtu'
    assert daemon.may_look(tmp_path / 'projects' / 'mesh.vtu')
    for private in (
        desktop,
        tmp_path / 'Library' / 'CloudStorage' / 'Dropbox' / 'mesh.vtu',
        tmp_path / 'Library' / 'Containers' / 'com.example' / 'mesh.vtu',
        Path('/Volumes/disk/mesh.vtu'),
    ):
        assert not daemon.may_look(private)
    daemon._reachable_folders[str(tmp_path / 'Desktop' / 'other')] = (0.0, False)
    assert not daemon.may_look(desktop)
    # macOS grants the Desktop as a whole, so any folder in it read once vouches for all.
    daemon._reachable_folders[str(tmp_path / 'Desktop' / 'other')] = (0.0, True)
    assert daemon.may_look(desktop)
    assert not daemon.may_look(tmp_path / 'Documents' / 'mesh.vtu')
    daemon._reachable_folders['/Volumes/disk/a'] = (0.0, True)
    assert daemon.may_look(Path('/Volumes/disk/b/mesh.vtu'))
    assert not daemon.may_look(Path('/Volumes/other/mesh.vtu'))


def test_pruner_runs_on_a_daemon_thread(monkeypatch):
    """The pruner never keeps the service from stopping."""
    monkeypatch.setattr(daemon, 'prune_periodically', lambda: None)
    thread = daemon.prune_in_background()
    thread.join(5)
    assert thread.daemon
