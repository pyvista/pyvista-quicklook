"""Tests for how the render service answers one request: handle, respond, and produce."""

from __future__ import annotations

import contextlib
import json
import os
from pathlib import Path
import threading
import time
from types import SimpleNamespace

from conftest import reachable
from conftest import request
from conftest import settings
from conftest import wait_for
import pytest

from pyvista_quicklook import config
from pyvista_quicklook import convert
from pyvista_quicklook import daemon
from pyvista_quicklook import environment
from pyvista_quicklook import render
from pyvista_quicklook.environment import RenderError


def test_write_json_is_atomic(tmp_path):
    """Replies appear complete or not at all."""
    target = tmp_path / 'reply.pvqlrep'
    daemon.write_json(target, {'ok': True})
    assert json.loads(target.read_text()) == {'ok': True}
    assert list(tmp_path.iterdir()) == [target]


def test_a_request_being_written_is_not_one_the_service_picks_up(tmp_path, monkeypatch):
    """Until it is renamed into place, a request's file does not end in the request suffix."""
    written = []
    write_text = Path.write_text
    monkeypatch.setattr(
        Path, 'write_text', lambda self, text: written.append(self) or write_text(self, text)
    )
    daemon.write_json(tmp_path / f'token{daemon.REQUEST_SUFFIX}', {'path': 'mesh.vtu'})
    assert written
    assert not any(path.name.endswith(daemon.REQUEST_SUFFIX) for path in written)


def test_handle_reports_a_malformed_request(tmp_path):
    """A request that is not valid JSON produces an error reply and is taken away."""
    asked = tmp_path / 'token.pvqlreq'
    asked.write_text('not json')
    daemon.handle(asked)
    reply = json.loads((tmp_path / 'token.pvqlrep').read_text())
    assert reply['ok'] is False
    assert reply['error'].startswith('Malformed preview request')
    assert not asked.exists()


@pytest.mark.parametrize('body', [[{'path': '/data/a.vtu'}], {'path': 7}, {'size': 1}])
def test_handle_reports_a_request_that_names_no_path(tmp_path, body):
    """A payload that is not an object naming a path is refused as malformed."""
    asked = tmp_path / 'token.pvqlreq'
    asked.write_text(json.dumps(body))
    daemon.handle(asked)
    reply = json.loads((tmp_path / 'token.pvqlrep').read_text())
    assert reply['ok'] is False
    assert reply['error'].startswith('Malformed preview request')


def test_handle_reads_an_identity_written_as_decimals(tmp_path, monkeypatch):
    """A time or size spelled with a fraction is read as the whole number it holds."""
    settings(monkeypatch, {})
    reachable(monkeypatch, True)
    seen = []

    def produce(target, config, identity):
        seen.append(identity)
        message = 'stop'
        raise RenderError(message)

    monkeypatch.setattr(daemon, 'produce', produce)
    request(tmp_path, path='/data/a.vtu', mtime='12.5', size='3.0')
    assert seen == [('/data/a.vtu', 12, 3)]


def test_handle_marks_a_file_that_holds_no_mesh(tmp_path, monkeypatch):
    """Only a file the exporter cannot read as a mesh is marked for showing as something else."""

    def unreadable(target, config, identity=None):
        message = 'Converting notes.dat failed.'
        raise environment.NotAMeshError(message)

    settings(monkeypatch, {})
    reachable(monkeypatch, True)
    monkeypatch.setattr(daemon, 'produce', unreadable)
    reply = request(tmp_path, path=str(tmp_path / 'notes.dat'))
    assert reply == {'ok': False, 'error': 'Converting notes.dat failed.', 'not_a_mesh': True}


def test_handle_reports_an_unexpected_exception(tmp_path, monkeypatch):
    """An error that is not a RenderError is reported with its type, not raised."""

    def explode(target, config, identity=None):
        message = 'boom'
        raise ValueError(message)

    settings(monkeypatch, {})
    reachable(monkeypatch, True)
    monkeypatch.setattr(daemon, 'produce', explode)
    reply = request(tmp_path, path=str(tmp_path / 'mesh.vtu'))
    assert reply == {'ok': False, 'error': 'ValueError: boom'}
    assert not (tmp_path / 'token.pvqlreq').exists()


def test_handle_delivers_a_still_image(tmp_path, monkeypatch):
    """A still image is copied beside the reply and named under ``png``."""
    png = tmp_path / 'cached.png'
    png.write_bytes(b'png')
    settings(monkeypatch, {})
    reachable(monkeypatch, True)
    monkeypatch.setattr(daemon, 'produce', lambda target, config, identity: png)
    monkeypatch.setattr(daemon.convert_mod, 'describe', lambda *args, **kwargs: {'type': 'X'})
    reply = request(tmp_path, path=str(tmp_path / 'mesh.vtu'))
    assert reply['ok'] is True
    assert Path(reply['png']).read_bytes() == b'png'
    assert reply['info'] == {'type': 'X'}


def test_handle_delivers_an_interactive_scene(tmp_path, monkeypatch):
    """When a scene is built, the reply points at it rather than an image."""
    ply = tmp_path / 'scene.ply'
    ply.write_bytes(b'ply\n')
    reachable(monkeypatch, True)
    monkeypatch.setattr(daemon.convert_mod, 'scene', lambda target, config, identity=None: ply)
    reply = request(tmp_path, path=str(tmp_path / 'mesh.vtu'), mtime=1, size=2, scene=True)
    assert reply['ok'] is True
    assert reply['scene'].endswith('token.ply')
    assert (tmp_path / 'token.ply').read_bytes() == b'ply\n'


def test_handle_reports_why_the_scene_failed(tmp_path, monkeypatch):
    """The scene's own error is what gets reported."""

    def no_scene(target, config, identity=None):
        message = 'the dataset has no surface to show'
        raise RenderError(message)

    reachable(monkeypatch, True)
    monkeypatch.setattr(daemon.convert_mod, 'scene', no_scene)
    reply = request(tmp_path, path=str(tmp_path / 'mesh.vtu'), mtime=1, size=2, scene=True)
    assert reply['ok'] is False
    assert 'no surface' in reply['error']


def test_handle_falls_back_to_the_staged_copy(tmp_path, monkeypatch):
    """When the original cannot be read, the copy is converted under the original's identity."""
    ply = tmp_path / 'built.ply'
    ply.write_bytes(b'ply\n')
    seen = {}

    def fake_scene(source, config, identity=None):
        seen['source'] = str(source)
        seen['identity'] = identity
        return ply

    monkeypatch.setattr(daemon.convert_mod, 'scene', fake_scene)
    reachable(monkeypatch, False)
    reply = request(
        tmp_path,
        path='/private/mesh.vtu',
        copy=str(tmp_path / 'copy.vtu'),
        mtime=7,
        size=3,
        scene=True,
    )
    assert seen['source'] == str(tmp_path / 'copy.vtu')
    assert seen['identity'] == ('/private/mesh.vtu', 7, 3)
    assert reply['ok'] is True
    assert (tmp_path / 'token.ply').read_bytes() == b'ply\n'


def test_handle_reports_unreachable_files_without_a_copy(tmp_path, monkeypatch):
    """A request with no copy for an unreadable file explains the problem."""
    reachable(monkeypatch, False)
    reply = request(tmp_path, path='/private/mesh.vtu')
    assert reply['ok'] is False
    assert 'Privacy & Security' in reply['error']
    # The extension copies a file only when asked, then requests it again with the copy.
    assert reply['needs_copy'] is True


def test_handle_previews_the_copy_of_an_unreadable_file(tmp_path, monkeypatch):
    """A request that brings a copy of a file in an unreadable folder previews the copy."""
    png = tmp_path / 'cached.png'
    png.write_bytes(b'png')
    built = []
    settings(monkeypatch, {'info_panel': 'hidden'})
    reachable(monkeypatch, False)
    monkeypatch.setattr(
        daemon, 'produce', lambda target, config, identity: built.append(target) or png
    )
    reply = request(tmp_path, path='/private/mesh.vtu', copy='/staged/mesh.vtu')
    assert reply['ok'] is True
    assert built == ['/staged/mesh.vtu']


def test_handle_refuses_a_large_file_before_converting_it(tmp_path, monkeypatch):
    """Above the limit a file is not converted, only its header read, and the reply says so."""
    settings(monkeypatch, {'max_file_size_mb': 1})
    reachable(monkeypatch, True)
    monkeypatch.setattr(daemon, 'produce', lambda *args: pytest.fail('converted'))
    monkeypatch.setattr(daemon.convert_mod, 'header', lambda source, config: {'type': 'X'})
    reply = request(tmp_path, path='/data/huge.vtu', mtime=1, size=5_000_000_000)
    assert reply['ok'] is False
    assert 'pvql config set max_file_size_mb 5120' in reply['error']
    assert reply['too_large'] is True
    assert 'not_a_mesh' not in reply
    assert reply['info'] == {'type': 'X'}


def test_handle_refuses_an_unreachable_large_file_without_reading_it(tmp_path, monkeypatch):
    """A large file the service cannot reach is refused without a description."""
    settings(monkeypatch, {'max_file_size_mb': 1})
    reachable(monkeypatch, False)
    monkeypatch.setattr(daemon.convert_mod, 'header', lambda *args: pytest.fail('read attempted'))
    reply = request(tmp_path, path='/private/huge.vtu', mtime=1, size=5 * 2**30)
    assert reply['too_large'] is True
    assert 'info' not in reply


def test_handle_previews_a_large_file_when_forced(tmp_path, monkeypatch):
    """A forced request skips the size limit."""
    png = tmp_path / 'cached.png'
    png.write_bytes(b'png')
    settings(monkeypatch, {'max_file_size_mb': 1, 'info_panel': 'hidden'})
    reachable(monkeypatch, True)

    def produce(target, config, identity):
        environment.check_size(identity, config)
        return png

    monkeypatch.setattr(daemon, 'produce', produce)
    reply = request(tmp_path, path='/data/huge.vtu', mtime=1, size=5 * 2**30, force=True)
    assert reply['ok'] is True


def test_a_large_file_previewed_once_is_not_refused_again(tmp_path, monkeypatch):
    """Once Preview Anyway has built a file, it is served again, even from a private folder."""
    png = tmp_path / 'cached.png'
    png.write_bytes(b'png')
    settings(monkeypatch, {'max_file_size_mb': 1})
    reachable(monkeypatch, False)
    monkeypatch.setattr(daemon, 'cached_preview', lambda identity, config: png)
    monkeypatch.setattr(daemon, 'produce', lambda *args: pytest.fail('built again'))
    monkeypatch.setattr(daemon.convert_mod, 'header', lambda *args: pytest.fail('header read'))
    monkeypatch.setattr(daemon.convert_mod, 'describe', lambda *args, **kwargs: {'type': 'X'})
    reply = request(tmp_path, path='/data/huge.vtu', mtime=1, size=5 * 10**9)
    assert reply['ok'] is True
    assert Path(reply['png']).read_bytes() == b'png'


@pytest.mark.parametrize(
    ('cached', 'is_reachable', 'expected'),
    [
        ({'type': 'cached'}, True, {'type': 'cached'}),
        (None, True, {'type': 'header'}),
        ({'type': 'cached'}, False, {'type': 'cached'}),
        (None, False, None),
    ],
)
def test_a_description_alone_converts_nothing(
    tmp_path, monkeypatch, cached, is_reachable, expected
):
    """A description alone comes from the cache, or from the header of a reachable file."""
    reads = []

    def describe(target, config, identity=None, *, read=True, timeout=None):
        reads.append(read)
        return cached

    settings(monkeypatch, {})
    reachable(monkeypatch, is_reachable)
    monkeypatch.setattr(daemon, 'produce', lambda *args: pytest.fail('converted'))
    monkeypatch.setattr(daemon.convert_mod, 'describe', describe)
    monkeypatch.setattr(daemon.convert_mod, 'header', lambda source, config: {'type': 'header'})
    reply = request(tmp_path, path='/data/big.pv', describe=True, mtime=1, size=2)
    assert reply == {'ok': True, 'info': expected}
    assert all(read is False for read in reads)


def test_a_description_from_the_header_is_read_once(tmp_path, monkeypatch):
    """A file described again from its header is answered without reading the header again."""
    headers = []
    settings(monkeypatch, {})
    reachable(monkeypatch, True)
    monkeypatch.setattr(daemon.convert_mod, 'describe', lambda *args, **kwargs: None)
    monkeypatch.setattr(
        daemon.convert_mod,
        'header',
        lambda source, config: headers.append(source) or {'type': 'H'},
    )
    for size in (2, 2, 3):
        reply = request(tmp_path, path='/data/big.pv', describe=True, mtime=1, size=size)
        assert reply == {'ok': True, 'info': {'type': 'H'}}
    assert headers == ['/data/big.pv', '/data/big.pv']


@pytest.mark.parametrize(
    ('stored', 'payload', 'interactive'),
    [
        ({}, {}, True),
        ({}, {'scene': False}, False),
        ({'interactive': False}, {}, False),
        ({'interactive': False}, {'scene': True}, True),
        ({'interactive': False, 'theme': '/themes/mine.json'}, {}, True),
        ({'theme': '/themes/mine.json'}, {'scene': False}, True),
    ],
)
def test_previews_follow_the_settings_unless_the_view_is_asked_for(
    tmp_path, monkeypatch, stored, payload, interactive
):
    """A preview follows the settings or the view asked for, but a saved theme is drawn in 3D."""
    asked = []

    def produce(target, config, identity):
        asked.append(config.get('interactive', daemon.config_mod.DEFAULTS['interactive']))
        message = 'stop'
        raise RenderError(message)

    settings(monkeypatch, stored)
    reachable(monkeypatch, True)
    monkeypatch.setattr(daemon, 'produce', produce)
    request(tmp_path, path='/data/mesh.vtu', **payload)
    assert asked == [interactive]


def test_a_still_image_is_sent_before_its_description(tmp_path, monkeypatch):
    """An image whose description is not cached is sent at once and described afterwards."""
    png = tmp_path / 'cached.png'
    png.write_bytes(b'png')
    reply = tmp_path / 'token.pvqlrep'
    replied_first = []

    def describe(target, config, identity=None, *, read=True, timeout=None):
        if read:
            replied_first.append(reply.exists())

    settings(monkeypatch, {})
    reachable(monkeypatch, True)
    monkeypatch.setattr(daemon, 'produce', lambda target, config, identity: png)
    monkeypatch.setattr(daemon.convert_mod, 'describe', describe)
    monkeypatch.setattr(daemon.convert_mod, 'info_path', lambda identity: tmp_path / 'x.json')
    assert 'info' not in request(tmp_path, path='/data/mesh.vtu', mtime=1, size=2)
    daemon._descriptions.join()
    assert replied_first == [True]


def test_handle_sends_the_header_ahead_of_a_slow_preview(tmp_path, monkeypatch):
    """A large file's header arrives first, and the preview after it."""
    png = tmp_path / 'cached.png'
    png.write_bytes(b'png')
    early = tmp_path / f'token{daemon.EARLY_SUFFIX}'
    settings(monkeypatch, {})
    reachable(monkeypatch, True)
    monkeypatch.setattr(daemon, '_headers', {})
    monkeypatch.setattr(daemon, 'cached_preview', lambda identity, config: None)
    monkeypatch.setattr(daemon.convert_mod, 'header', lambda source, config: {'type': 'Early'})
    monkeypatch.setattr(daemon.convert_mod, 'describe', lambda *args, **kwargs: {'type': 'Final'})

    def slow(target, config, identity):
        deadline = time.monotonic() + 10
        while not early.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        return png

    monkeypatch.setattr(daemon, 'produce', slow)
    reply = request(tmp_path, path='/data/big.vtu', mtime=1, size=daemon.EARLY_BYTES)
    assert json.loads(early.read_text()) == {'ok': True, 'info': {'type': 'Early'}}
    assert reply['info'] == {'type': 'Final'}


def test_a_forced_preview_also_sends_its_header_first(tmp_path, monkeypatch):
    """Previewing a file above the limit anyway still shows its header while it converts."""
    png = tmp_path / 'cached.png'
    png.write_bytes(b'png')
    early = tmp_path / f'token{daemon.EARLY_SUFFIX}'
    settings(monkeypatch, {'max_file_size_mb': 1})
    reachable(monkeypatch, True)
    monkeypatch.setattr(daemon, '_headers', {})
    monkeypatch.setattr(daemon, 'cached_preview', lambda identity, config: None)
    monkeypatch.setattr(daemon.convert_mod, 'header', lambda source, config: {'type': 'Early'})
    monkeypatch.setattr(daemon.convert_mod, 'describe', lambda *args, **kwargs: None)

    def slow(target, config, identity):
        environment.check_size(identity, config)
        deadline = time.monotonic() + 10
        while not early.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        return png

    monkeypatch.setattr(daemon, 'produce', slow)
    reply = request(tmp_path, path='/data/huge.vtu', mtime=1, size=5_000_000_000, force=True)
    assert json.loads(early.read_text())['info'] == {'type': 'Early'}
    assert reply['ok'] is True


def test_a_second_request_gets_its_header_while_the_first_converts(tmp_path, monkeypatch):
    """Quick Look asks twice for one file; the second waits for the mesh, not for the header."""
    png = tmp_path / 'cached.png'
    png.write_bytes(b'png')
    # Too little memory for two at once, so the second conversion waits for the first.
    settings(monkeypatch, {'max_conversion_memory_mb': 1})
    reachable(monkeypatch, True)
    monkeypatch.setattr(daemon, '_headers', {})
    monkeypatch.setattr(daemon, 'cached_preview', lambda identity, config: None)
    monkeypatch.setattr(daemon.convert_mod, 'header', lambda source, config: {'type': 'Early'})
    monkeypatch.setattr(daemon.convert_mod, 'describe', lambda *args, **kwargs: None)
    release = threading.Event()
    converting, peak = [], []

    def slow(target, config, identity):
        converting.append(identity)
        peak.append(len(converting))
        release.wait(10)
        converting.pop()
        return png

    monkeypatch.setattr(daemon, 'produce', slow)
    body = json.dumps({'path': '/data/big.vtu', 'mtime': 1, 'size': daemon.EARLY_BYTES})
    threads = []
    for token in ('first', 'second'):
        asked = tmp_path / f'{token}.pvqlreq'
        asked.write_text(body)
        threads.append(threading.Thread(target=daemon.handle, args=(asked,)))
        threads[-1].start()
    second_early = tmp_path / f'second{daemon.EARLY_SUFFIX}'
    deadline = time.monotonic() + 10
    while not second_early.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert second_early.exists()
    assert not (tmp_path / 'first.pvqlrep').exists()
    release.set()
    for thread in threads:
        thread.join(10)
    for token in ('first', 'second'):
        assert json.loads((tmp_path / f'{token}.pvqlrep').read_text())['ok'] is True
    assert max(peak) == 1, 'two previews were built at once'


def test_two_requests_for_one_large_file_read_its_header_once(tmp_path, monkeypatch):
    """Quick Look's second request for a large file is given the header the first one read."""
    png = tmp_path / 'cached.png'
    png.write_bytes(b'png')
    settings(monkeypatch, {})
    reachable(monkeypatch, True)
    monkeypatch.setattr(daemon, '_headers', {})
    monkeypatch.setattr(daemon, 'cached_preview', lambda identity, config: None)
    monkeypatch.setattr(daemon.convert_mod, 'describe', lambda *args, **kwargs: None)
    reads = []
    monkeypatch.setattr(
        daemon.convert_mod, 'header', lambda source, config: reads.append(source) or {'type': 'E'}
    )
    monkeypatch.setattr(daemon, 'produce', lambda target, config, identity: png)
    for _ in range(2):
        assert request(tmp_path, path='/data/big.vtu', mtime=1, size=daemon.EARLY_BYTES)['ok']
        wait_for(lambda: daemon._headers)
    assert reads == ['/data/big.vtu']
    assert daemon._headers == {('/data/big.vtu', 1, daemon.EARLY_BYTES): {'type': 'E'}}


def test_no_header_is_sent_after_the_preview(tmp_path, monkeypatch):
    """A header that comes back after the preview was sent is dropped."""
    png = tmp_path / 'cached.png'
    png.write_bytes(b'png')
    sent = threading.Event()
    settings(monkeypatch, {})
    reachable(monkeypatch, True)
    monkeypatch.setattr(daemon, '_headers', {})
    monkeypatch.setattr(daemon, 'cached_preview', lambda identity, config: None)
    monkeypatch.setattr(daemon, 'produce', lambda target, config, identity: png)
    # A description in hand, so the only thread started is the one sending the header.
    monkeypatch.setattr(daemon.convert_mod, 'describe', lambda *args, **kwargs: {'type': 'X'})
    threads = []
    monkeypatch.setattr(
        daemon.threading,
        'Thread',
        lambda target, args: threads.append((target, args)) or SimpleNamespace(start=sent.set),
    )
    request(tmp_path, path='/data/big.vtu', mtime=1, size=daemon.EARLY_BYTES)
    monkeypatch.setattr(daemon.convert_mod, 'header', lambda source, config: {'type': 'Late'})
    [(target, args)] = threads
    target(*args)
    assert sent.is_set()
    assert not (tmp_path / f'token{daemon.EARLY_SUFFIX}').exists()


def test_small_or_cached_files_get_no_early_header(tmp_path, monkeypatch):
    """Only a large file that is not yet cached is worth a header first."""
    png = tmp_path / 'cached.png'
    png.write_bytes(b'png')
    settings(monkeypatch, {})
    reachable(monkeypatch, True)
    monkeypatch.setattr(daemon, '_headers', {})
    monkeypatch.setattr(daemon, 'produce', lambda target, config, identity: png)
    monkeypatch.setattr(daemon.convert_mod, 'describe', lambda *args, **kwargs: None)
    monkeypatch.setattr(daemon.convert_mod, 'header', lambda *args: pytest.fail('header read'))
    for size, is_cached in ((daemon.EARLY_BYTES - 1, False), (daemon.EARLY_BYTES, True)):
        monkeypatch.setattr(
            daemon,
            'cached_preview',
            lambda identity, config, value=is_cached: png if value else None,
        )
        assert request(tmp_path, path='/data/big.vtu', mtime=1, size=size)['ok'] is True
    assert not (tmp_path / f'token{daemon.EARLY_SUFFIX}').exists()


def test_cached_looks_where_each_kind_of_preview_is_kept(tmp_path, monkeypatch):
    """A preview counts as cached once its file is there, whether or not it is described."""
    monkeypatch.setattr(config, 'CACHE_DIR', tmp_path)
    identity = ('/data/a.vtu', 1, 2)
    scene, image = {'interactive': True}, {'interactive': False}
    assert daemon.cached_preview(identity, scene) is None
    built = convert.scene_path(identity, scene)
    built.write_bytes(b'x')
    assert daemon.cached_preview(identity, scene) == built
    assert daemon.cached_preview(identity, {**scene, 'cache': False}) is None
    assert daemon.cached_preview(None, scene) is None
    assert daemon.cached_preview(identity, image) is None
    still = render.preview_path(identity, image)
    still.write_bytes(b'x')
    assert daemon.cached_preview(identity, image) == still


def test_an_empty_cached_entry_does_not_count_and_a_hit_is_logged(tmp_path, monkeypatch):
    """A zero-byte entry is built again, and serving a cached one is logged when logging is on."""
    monkeypatch.setattr(config, 'CACHE_DIR', tmp_path)
    logged = []
    monkeypatch.setattr(daemon.environment, 'log', lambda config, text: logged.append(text))
    identity = ('/data/a.vtu', 1, 2)
    built = convert.scene_path(identity, {'log': True})
    built.touch()
    assert daemon.cached_preview(identity, {'log': True}) is None
    assert logged == []
    built.write_bytes(b'x')
    assert daemon.cached_preview(identity, {'log': True}) == built
    assert logged == ['hit  /data/a.vtu']


def test_a_cached_preview_does_not_wait_for_another_conversion(tmp_path, monkeypatch):
    """Only previews still to be built queue for the conversion lock."""
    png = tmp_path / 'cached.png'
    png.write_bytes(b'png')
    settings(monkeypatch, {})
    reachable(monkeypatch, True)
    monkeypatch.setattr(daemon, 'cached_preview', lambda identity, config: png)
    monkeypatch.setattr(daemon, 'produce', lambda *args: pytest.fail('built again'))
    monkeypatch.setattr(daemon.convert_mod, 'describe', lambda *args, **kwargs: None)
    asked = tmp_path / 'token.pvqlreq'
    asked.write_text(json.dumps({'path': '/data/a.vtu', 'mtime': 1, 'size': 10}))
    with daemon.CONVERTING.reserve(1, 1):
        worker = threading.Thread(target=daemon.handle, args=(asked,))
        worker.start()
        worker.join(5)
        assert not worker.is_alive()
    assert json.loads((tmp_path / 'token.pvqlrep').read_text())['ok'] is True


def test_describing_a_still_image_holds_the_conversion_lock(tmp_path, monkeypatch):
    """A still image's description, read after the reply, still counts toward the budget."""
    png = tmp_path / 'still.png'
    png.write_bytes(b'png')
    held = []
    described = threading.Event()

    def describe(*args, read=True, **kwargs):
        if read:
            held.append(daemon.CONVERTING.running)
            described.set()

    settings(monkeypatch, {'interactive': False})
    reachable(monkeypatch, True)
    monkeypatch.setattr(daemon, 'produce', lambda target, config, identity: png)
    monkeypatch.setattr(daemon.convert_mod, 'describe', describe)
    request(tmp_path, path='/data/a.vtu', mtime=1, size=10)
    assert described.wait(5)
    assert held == [1]


def converting_together(tmp_path, monkeypatch, sizes, budget_mb):
    """Request files of the given sizes at once and return how many converted together."""
    settings(monkeypatch, {'max_conversion_memory_mb': budget_mb})
    reachable(monkeypatch, True)
    monkeypatch.setattr(daemon, 'cached_preview', lambda identity, config: None)
    monkeypatch.setattr(daemon.convert_mod, 'header', lambda source, config: None)
    monkeypatch.setattr(daemon.convert_mod, 'describe', lambda *args, **kwargs: None)
    png = tmp_path / 'built.png'
    png.write_bytes(b'png')
    running, peak = [], []
    together = threading.Barrier(len(sizes), timeout=0.2)

    def build(target, config, identity):
        running.append(target)
        peak.append(len(running))
        # Waits for the others to start too, which they do only if memory allows.
        with contextlib.suppress(threading.BrokenBarrierError):
            together.wait()
        running.remove(target)
        return png

    monkeypatch.setattr(daemon, 'produce', build)
    threads = []
    for index, size in enumerate(sizes):
        asked = tmp_path / f'r{index}.pvqlreq'
        asked.write_text(json.dumps({'path': f'/data/{index}.vtu', 'mtime': 1, 'size': size}))
        threads.append(threading.Thread(target=daemon.handle, args=(asked,), daemon=True))
        threads[-1].start()
    for thread in threads:
        thread.join(10)
    for index in range(len(sizes)):
        assert json.loads((tmp_path / f'r{index}.pvqlrep').read_text())['ok'] is True
    return max(peak)


def test_small_files_convert_side_by_side(tmp_path, monkeypatch):
    """Files whose conversions fit the memory budget together all start at once."""
    assert converting_together(tmp_path, monkeypatch, [1_000_000] * 3, budget_mb=1000) == 3


def test_a_file_too_big_for_the_budget_converts_alone(tmp_path, monkeypatch):
    """A conversion that would exceed the budget waits, then runs even if it alone exceeds it."""
    assert converting_together(tmp_path, monkeypatch, [1_000_000, 500_000_000], 1000) == 1


def test_a_small_file_converts_beside_a_big_one(tmp_path, monkeypatch):
    """A big conversion leaves room for a small one when the budget allows both."""
    sizes = [1_000_000_000, 1_000_000]
    assert converting_together(tmp_path, monkeypatch, sizes, budget_mb=7000) == 2


def test_two_requests_for_one_file_build_it_once(tmp_path, monkeypatch):
    """Quick Look's second request for a file waits for the first build and reuses it."""
    settings(monkeypatch, {})
    reachable(monkeypatch, True)
    monkeypatch.setattr(daemon.convert_mod, 'header', lambda source, config: None)
    monkeypatch.setattr(daemon.convert_mod, 'describe', lambda *args, **kwargs: None)
    png = tmp_path / 'built.png'
    builds, running, peak = [], [], []
    monkeypatch.setattr(
        daemon, 'cached_preview', lambda identity, config: png if png.exists() else None
    )

    def build(target, config, identity):
        running.append(target)
        peak.append(len(running))
        if not png.exists():
            builds.append(target)
            # Finishes only once the second request is waiting its turn for this file.
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and not any(
                entry[1] == 2 for entry in daemon._previewing.values()
            ):
                time.sleep(0.01)
            png.write_bytes(b'png')
        running.remove(target)
        return png

    monkeypatch.setattr(daemon, 'produce', build)
    threads = []
    for token in ('small', 'large'):
        asked = tmp_path / f'{token}.pvqlreq'
        asked.write_text(json.dumps({'path': '/data/a.vtu', 'mtime': 1, 'size': 10}))
        threads.append(threading.Thread(target=daemon.handle, args=(asked,)))
        threads[-1].start()
    for thread in threads:
        thread.join(10)
    assert builds == ['/data/a.vtu']
    assert max(peak) == 1
    for token in ('small', 'large'):
        assert json.loads((tmp_path / f'{token}.pvqlrep').read_text())['ok'] is True
    assert daemon._previewing == {}


def test_a_handler_that_dies_still_replies(tmp_path, monkeypatch):
    """An error outside the handler's own guard is reported, not lost with the thread."""
    monkeypatch.setattr(daemon, 'handle', lambda request: 1 / 0)
    taken = tmp_path / 'token.pvqltaken'
    taken.write_text('{}')
    daemon.answer(taken)
    reply = json.loads((tmp_path / 'token.pvqlrep').read_text())
    assert reply['ok'] is False
    assert reply['error'].startswith('ZeroDivisionError')
    assert not taken.exists()


def test_a_handler_that_replied_before_dying_keeps_its_reply(tmp_path, monkeypatch):
    """A reply already written is not replaced by the error that followed it."""

    def reply_then_die(request):
        request.with_suffix(daemon.REPLY_SUFFIX).write_text('{"ok": true}')
        raise ZeroDivisionError

    monkeypatch.setattr(daemon, 'handle', reply_then_die)
    taken = tmp_path / 'token.pvqltaken'
    taken.write_text('{}')
    daemon.answer(taken)
    assert json.loads((tmp_path / 'token.pvqlrep').read_text()) == {'ok': True}


def test_produce_skips_the_scene_when_not_interactive(monkeypatch, tmp_path):
    """Turning interactivity off goes straight to the rendered image."""
    png = tmp_path / 'out.png'

    def no_scene(target, config, identity=None):
        pytest.fail('the scene must not be built')

    monkeypatch.setattr(daemon.convert_mod, 'scene', no_scene)
    monkeypatch.setattr(daemon.render_mod, 'preview', lambda target, config, identity=None: png)
    assert daemon.produce('/mesh.vtu', {'interactive': False}) == png


def test_produce_does_not_hide_a_failed_conversion_behind_an_image(monkeypatch):
    """A dataset that cannot be converted is reported, not painted as a still image."""

    def fail(target, config, identity=None):
        message = 'no surface'
        raise RenderError(message)

    monkeypatch.setattr(daemon.convert_mod, 'scene', fail)
    monkeypatch.setattr(daemon.render_mod, 'preview', lambda *a, **k: pytest.fail('rendered'))
    with pytest.raises(RenderError, match='no surface'):
        daemon.produce('/mesh.vtu', {'interactive': True})


def test_a_copy_is_described_in_the_time_left(tmp_path, monkeypatch):
    """A staged copy goes once the reply is read, so it is described before, within the wait."""
    png = tmp_path / 'cached.png'
    png.write_bytes(b'png')
    reads = []

    def describe(target, config, identity=None, *, read=True, timeout=None):
        reads.append((target, read, timeout))
        return {'type': 'X'} if read else None

    settings(monkeypatch, {})
    reachable(monkeypatch, False)
    monkeypatch.setattr(daemon, 'produce', lambda target, config, identity: png)
    monkeypatch.setattr(daemon.convert_mod, 'describe', describe)
    reply = request(tmp_path, path='/private/mesh.vtu', copy='/staged/mesh.vtu')
    assert reply['info'] == {'type': 'X'}
    [cached, read] = reads
    assert cached == ('/staged/mesh.vtu', False, None)
    assert read[:2] == ('/staged/mesh.vtu', True)
    assert 1 < read[2] <= daemon.reply_seconds({}) - daemon.REPLY_MARGIN_SECONDS


def test_a_copy_is_not_described_once_the_wait_is_over(tmp_path, monkeypatch):
    """A request that has waited out the extension's patience is answered without a panel."""
    png = tmp_path / 'cached.png'
    png.write_bytes(b'png')
    reads = []
    settings(monkeypatch, {})
    reachable(monkeypatch, False)
    monkeypatch.setattr(daemon, 'produce', lambda target, config, identity: png)
    monkeypatch.setattr(
        daemon.convert_mod, 'describe', lambda *args, read=True, **kwargs: reads.append(read)
    )
    asked = tmp_path / 'token.pvqlreq'
    asked.write_text(json.dumps({'path': '/private/mesh.vtu', 'copy': '/staged/mesh.vtu'}))
    stamp = time.time() - daemon.reply_seconds({})
    os.utime(asked, (stamp, stamp))
    daemon.handle(asked)
    assert 'info' not in json.loads((tmp_path / 'token.pvqlrep').read_text())
    assert reads == [False]


@pytest.fixture
def vanishing(tmp_path, monkeypatch):
    """Serve a request whose cached preview is pruned before it is copied; record builds."""
    png = tmp_path / 'built.png'
    png.write_bytes(b'png')
    found = iter([tmp_path / 'gone.png', tmp_path / 'gone.png', None])
    settings(monkeypatch, {})
    monkeypatch.setattr(daemon, 'cached_preview', lambda identity, config: next(found))
    monkeypatch.setattr(daemon.convert_mod, 'describe', lambda *args, **kwargs: None)
    monkeypatch.setattr(daemon, 'describe_later', lambda *args: None)
    built, locked = [], []
    held = threading.local()
    original = daemon.one_at_a_time

    @contextlib.contextmanager
    def watched(identity, config):
        with original(identity, config):
            held.inside = True
            try:
                yield
            finally:
                held.inside = False

    def produce(target, config, identity):
        built.append(target)
        locked.append(getattr(held, 'inside', False))
        return png

    monkeypatch.setattr(daemon, 'one_at_a_time', watched)
    monkeypatch.setattr(daemon, 'produce', produce)

    def ask(*, is_reachable, **extra):
        reachable(monkeypatch, is_reachable)
        return request(tmp_path, path=str(tmp_path / 'mesh.vtu'), mtime=1, size=2, **extra)

    return SimpleNamespace(ask=ask, built=built, locked=locked)


def test_a_preview_pruned_before_it_is_copied_is_built_again(vanishing, tmp_path):
    """A cached preview that goes before it is copied is rebuilt, under the per-file lock."""
    reply = vanishing.ask(is_reachable=True)
    assert Path(reply['png']).read_bytes() == b'png'
    assert vanishing.built == [str(tmp_path / 'mesh.vtu')]
    assert vanishing.locked == [True]


def test_a_pruned_preview_of_an_unreadable_file_is_built_from_the_copy(vanishing, tmp_path):
    """An unreadable file is never read for a rebuild; the extension's copy is."""
    copy = str(tmp_path / 'staged' / 'mesh.vtu')
    reply = vanishing.ask(is_reachable=False, copy=copy)
    assert reply['ok'] is True
    assert vanishing.built == [copy]


def test_a_pruned_preview_of_an_unreadable_file_asks_for_a_copy(vanishing):
    """Without a copy, the extension is asked for one rather than the file being read."""
    reply = vanishing.ask(is_reachable=False)
    assert reply['needs_copy'] is True
    assert vanishing.built == []
