"""Tests for the parts of pyvista_quicklook that do not render."""

from __future__ import annotations

import argparse
import contextlib
import json
import os
from pathlib import Path
import plistlib
import re
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from pyvista_quicklook import cache
from pyvista_quicklook import cli
from pyvista_quicklook import config
from pyvista_quicklook import convert
from pyvista_quicklook import daemon
from pyvista_quicklook import environment
from pyvista_quicklook import formats
from pyvista_quicklook import plist
from pyvista_quicklook import render
from pyvista_quicklook import warmup


def test_normalize_accepts_any_spelling():
    """Extensions are lowercased and given exactly one leading dot."""
    assert formats.normalize('VTU') == '.vtu'
    assert formats.normalize('.VtU ') == '.vtu'


def test_every_known_format_is_claimed_by_default():
    """The format table and the default claims are the same set, and exclude macOS's own."""
    assert set(formats.default_extensions()) == set(formats.FORMATS)
    assert '.vtu' in formats.FORMATS
    assert '.stl' not in formats.FORMATS
    assert set(formats.FORMATS).isdisjoint(formats.UNCLAIMED)


def test_resolve_extensions_applies_config():
    """Additions and removals change the claimed set."""
    resolved = formats.resolve_extensions(add=['msh'], remove=['.vtu'])
    assert '.msh' in resolved
    assert '.vtu' not in resolved


def test_readme_lists_exactly_the_known_formats():
    """The README's Supported files section names every format and every unclaimed one."""
    readme = (Path(__file__).parents[1] / 'README.md').read_text()
    section = readme.split('## Supported files', 1)[1].split('\n## ', 1)[0]
    listed = set(re.findall(r'`(\.[a-z0-9.]+)`', section))
    known = set(formats.FORMATS) | set(formats.UNCLAIMED) | set(formats.VIA_SYSTEM_TYPE)
    assert listed == known | set(formats.NATIVE_TYPES)


def test_unclaimed_extensions_say_why():
    """Every extension left unclaimed carries a reason."""
    assert all(formats.UNCLAIMED.values())


def test_types_lists_an_added_extension_it_does_not_know(capsys, monkeypatch):
    """An extension added through the config is listed even when the table lacks it."""
    config = {**cli.config_mod.DEFAULTS, 'extensions': {'add': ['.xyz'], 'remove': []}}
    monkeypatch.setattr(cli.config_mod, 'load', lambda: config)
    assert cli.cmd_types(argparse.Namespace(all=False)) == 0
    assert '.xyz' in capsys.readouterr().out


def test_uti_is_unique_per_extension():
    """No two extensions share a uniform type identifier."""
    utis = [formats.uti_for(ext) for ext in formats.FORMATS]
    assert len(utis) == len(set(utis))


def test_app_plist_declares_every_extension():
    """The app exports one uniform type per claimed extension."""
    extensions = ['.vtu', '.vtp']
    declarations = plist.app_plist(extensions)['UTExportedTypeDeclarations']
    tags = [d['UTTypeTagSpecification']['public.filename-extension'] for d in declarations]
    assert tags == [['vtu'], ['vtp']]


def test_extension_plist_matches_the_app():
    """The extension supports exactly the types the app exports."""
    extensions = ['.vtu', '.vtp']
    app = plist.app_plist(extensions)
    ext = plist.extension_plist(extensions)
    exported = [d['UTTypeIdentifier'] for d in app['UTExportedTypeDeclarations']]
    supported = ext['NSExtension']['NSExtensionAttributes']['QLSupportedContentTypes']
    assert exported == supported[: len(exported)]
    assert ext['NSExtension']['NSExtensionPrincipalClass'] == plist.EXT_PRINCIPAL_CLASS
    assert ext['NSExtension']['NSExtensionPointIdentifier'] == 'com.apple.quicklook.preview'


def test_scenes_blend_colours_as_vtk_does():
    """The app and the extension both turn off SceneKit's linear-light rendering."""
    assert plist.app_plist(['.vtu'])['SCNDisableLinearSpaceRendering'] is True
    assert plist.extension_plist(['.vtu'])['SCNDisableLinearSpaceRendering'] is True


def test_thumbnail_plist_covers_only_types_that_carry_a_picture():
    """The Finder asks the thumbnail extension only for claimed types that can embed one."""
    thumb = plist.thumbnail_plist(['.pv', '.vtu'])
    attributes = thumb['NSExtension']['NSExtensionAttributes']
    assert attributes['QLSupportedContentTypes'] == [formats.uti_for('.pv')]
    assert thumb['NSExtension']['NSExtensionPointIdentifier'] == 'com.apple.quicklook.thumbnail'
    assert thumb['NSExtension']['NSExtensionPrincipalClass'] == plist.THUMB_PRINCIPAL_CLASS
    assert (
        plist.thumbnail_plist(['.vtu'])['NSExtension']['NSExtensionAttributes'][
            'QLSupportedContentTypes'
        ]
        == []
    )


def test_system_types_are_supported_but_not_exported():
    """A type macOS declares is claimed by the extension and never exported by the app."""
    app = plist.app_plist(['.vtu'])
    ext = plist.extension_plist(['.vtu'])
    supported = ext['NSExtension']['NSExtensionAttributes']['QLSupportedContentTypes']
    exported = [d['UTTypeIdentifier'] for d in app['UTExportedTypeDeclarations']]
    for uti in formats.SYSTEM_TYPES:
        assert uti in supported
        assert uti not in exported


def test_the_app_can_open_its_own_types_and_the_native_ones():
    """The app opens what it claims and macOS's own 3D types, which Quick Look keeps to."""
    app = plist.app_plist(['.vtu'])
    ext = plist.extension_plist(['.vtu'])
    supported = ext['NSExtension']['NSExtensionAttributes']['QLSupportedContentTypes']
    exported = [d['UTTypeIdentifier'] for d in app['UTExportedTypeDeclarations']]
    [documents] = app['CFBundleDocumentTypes']
    for ext_name, uti in formats.NATIVE_TYPES.items():
        assert uti.startswith('public.'), ext_name
        # Quick Look never asks the extension for these, so it does not offer to preview them.
        assert uti not in supported
        assert uti not in exported
    # Listed under Open With, below an app that declares a type as its default.
    assert documents['LSHandlerRank'] == 'Alternate'
    assert set(documents['LSItemContentTypes']) == set(exported) | set(
        formats.NATIVE_TYPES.values()
    )
    assert set(formats.NATIVE_TYPES).isdisjoint(formats.FORMATS)
    assert set(formats.NATIVE_TYPES).isdisjoint(formats.UNCLAIMED)


def test_plists_are_writable(tmp_path):
    """Generated plists round-trip through plistlib."""
    target = tmp_path / 'Info.plist'
    plist.write(plist.app_plist(['.vtu']), target)
    assert plistlib.loads(target.read_bytes())['CFBundleIdentifier'] == plist.APP_BUNDLE_ID


def test_cache_key_follows_identity_not_the_rendered_file():
    """Two copies of one file share a preview when they carry the same identity."""
    config = {'window_size': [8, 8]}
    identity = ('/somewhere/mesh.vtu', 1234, 99)
    assert render.cache_key(identity, config) == render.cache_key(identity, config)
    assert render.cache_key(('/other.vtu', 1234, 99), config) != render.cache_key(identity, config)
    assert render.cache_key(('/somewhere/mesh.vtu', 1235, 99), config) != render.cache_key(
        identity, config
    )


def test_cache_key_follows_render_settings():
    """Changing the rendered size invalidates the preview."""
    identity = ('/mesh.vtu', 1, 2)
    assert render.cache_key(identity, {'window_size': [8, 8]}) != render.cache_key(
        identity, {'window_size': [9, 9]}
    )


def test_build_command_renders_off_screen():
    """The render command never opens a window."""
    command = render.build_command('/bin/pyvista', 'in.vtu', 'out.png', {'window_size': [64, 32]})
    assert command[:2] == ['/bin/pyvista', 'plot']
    assert '--off-screen' in command
    assert '--no-interactive' in command
    assert command[command.index('--window-size') + 1 : command.index('--window-size') + 3] == [
        '64',
        '32',
    ]


def test_build_command_passes_background_and_extra_args():
    """Optional settings reach the command line."""
    config = {'window_size': [1, 1], 'background': 'black', 'extra_args': ['--zoom', '2']}
    command = render.build_command('pyvista', 'in.vtu', 'out.png', config)
    assert '--background' in command
    assert command[-2:] == ['--zoom', '2']


def test_preview_rejects_files_above_the_limit(tmp_path):
    """A large file reports the limit instead of rendering."""
    sample = tmp_path / 'big.vtu'
    sample.write_bytes(b'x')
    config = {'max_file_size_mb': 1, 'window_size': [1, 1]}
    with pytest.raises(environment.RenderError, match='preview limit'):
        render.preview(sample, config, identity=(str(sample), 0, 5 * 1024 * 1024))


@pytest.mark.parametrize(
    ('size', 'refused'), [(2_048_000_000, False), (2_048_000_001, True), (2_510_000_000, True)]
)
def test_the_limit_counts_megabytes_as_the_finder_does(size, refused):
    """Sizes are decimal megabytes, so the message agrees with Get Info."""
    identity = ('/data/big.pv', 0, size)
    if not refused:
        environment.check_size(identity, {'max_file_size_mb': 2048})
        return
    with pytest.raises(environment.RenderError) as raised:
        environment.check_size(identity, {'max_file_size_mb': 2048})
    if size == 2_510_000_000:
        assert str(raised.value).startswith('big.pv is 2510 MB, above the 2048 MB preview limit.')
        assert str(raised.value).endswith('pvql config set max_file_size_mb 2560')


def test_preview_reports_a_missing_file(tmp_path):
    """A path that does not exist is reported plainly."""
    with pytest.raises(environment.RenderError, match='No such file'):
        render.preview(tmp_path / 'absent.vtu', {})


def test_write_json_is_atomic(tmp_path):
    """Replies appear complete or not at all."""
    target = tmp_path / 'reply.pvqlrep'
    daemon.write_json(target, {'ok': True})
    assert json.loads(target.read_text()) == {'ok': True}
    assert list(tmp_path.iterdir()) == [target]


def test_readable_rejects_a_missing_file(tmp_path):
    """The guard reports unreadable files without raising."""
    assert daemon.readable(str(tmp_path / 'nope')) is False


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


def test_handle_reports_a_malformed_request(tmp_path):
    """A request that is not valid JSON produces an error reply."""
    request = tmp_path / 'token.pvqlreq'
    request.write_text('not json')
    daemon.handle(request)
    reply = json.loads((tmp_path / 'token.pvqlrep').read_text())
    assert reply['ok'] is False
    assert not request.exists()


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
    monkeypatch.setattr(daemon, 'folder_is_reachable', lambda path: False)

    request = tmp_path / 'token.pvqlreq'
    request.write_text(
        json.dumps(
            {
                'path': '/private/mesh.vtu',
                'copy': str(tmp_path / 'copy.vtu'),
                'mtime': 7,
                'size': 3,
            }
        )
    )
    daemon.handle(request)

    assert seen['source'] == str(tmp_path / 'copy.vtu')
    assert seen['identity'] == ('/private/mesh.vtu', 7, 3)
    reply = json.loads((tmp_path / 'token.pvqlrep').read_text())
    assert reply['ok'] is True
    assert (tmp_path / 'token.ply').read_bytes() == b'ply\n'


def test_handle_reports_unreachable_files_without_a_copy(tmp_path, monkeypatch):
    """A request with no copy for an unreadable file explains the problem."""
    monkeypatch.setattr(daemon, 'folder_is_reachable', lambda path: False)
    request = tmp_path / 'token.pvqlreq'
    request.write_text(json.dumps({'path': '/private/mesh.vtu'}))
    daemon.handle(request)
    reply = json.loads((tmp_path / 'token.pvqlrep').read_text())
    assert reply['ok'] is False
    assert 'Privacy & Security' in reply['error']
    # The extension copies a file only when asked, then requests it again with the copy.
    assert reply['needs_copy'] is True


def test_handle_previews_the_copy_of_an_unreadable_file(tmp_path, monkeypatch):
    """A request that brings a copy of a file in an unreadable folder previews the copy."""
    png = tmp_path / 'cached.png'
    png.write_bytes(b'png')
    built = []
    monkeypatch.setattr(daemon.config_mod, 'load', lambda: {'info_panel': 'hidden'})
    monkeypatch.setattr(daemon, 'folder_is_reachable', lambda path: False)
    monkeypatch.setattr(
        daemon, 'produce', lambda target, config, identity: built.append(target) or png
    )
    request = tmp_path / 'token.pvqlreq'
    request.write_text(json.dumps({'path': '/private/mesh.vtu', 'copy': '/staged/mesh.vtu'}))
    daemon.handle(request)
    assert json.loads((tmp_path / 'token.pvqlrep').read_text())['ok'] is True
    assert built == ['/staged/mesh.vtu']


def test_handle_refuses_a_large_file_before_converting_it(tmp_path, monkeypatch):
    """Above the limit a file is not converted, only its header read, and the reply says so."""
    monkeypatch.setattr(daemon.config_mod, 'load', lambda: {'max_file_size_mb': 1})
    monkeypatch.setattr(daemon, 'folder_is_reachable', lambda path: True)
    monkeypatch.setattr(daemon, 'produce', lambda *args: pytest.fail('converted'))
    monkeypatch.setattr(daemon.convert_mod, 'header', lambda source, config: {'type': 'X'})
    request = tmp_path / 'token.pvqlreq'
    request.write_text(json.dumps({'path': '/data/huge.vtu', 'mtime': 1, 'size': 5_000_000_000}))
    daemon.handle(request)
    reply = json.loads((tmp_path / 'token.pvqlrep').read_text())
    assert reply['ok'] is False
    assert 'pvql config set max_file_size_mb 5120' in reply['error']
    assert reply['too_large'] is True
    assert 'not_a_mesh' not in reply
    assert reply['info'] == {'type': 'X'}


def test_handle_refuses_an_unreachable_large_file_without_reading_it(tmp_path, monkeypatch):
    """A large file the service cannot reach is refused without a description."""
    monkeypatch.setattr(daemon.config_mod, 'load', lambda: {'max_file_size_mb': 1})
    monkeypatch.setattr(daemon, 'folder_is_reachable', lambda path: False)
    monkeypatch.setattr(daemon.convert_mod, 'header', lambda *args: pytest.fail('read attempted'))
    request = tmp_path / 'token.pvqlreq'
    request.write_text(json.dumps({'path': '/private/huge.vtu', 'mtime': 1, 'size': 5 * 2**30}))
    daemon.handle(request)
    reply = json.loads((tmp_path / 'token.pvqlrep').read_text())
    assert reply['too_large'] is True
    assert 'info' not in reply


def test_handle_previews_a_large_file_when_forced(tmp_path, monkeypatch):
    """A forced request skips the size limit."""
    png = tmp_path / 'cached.png'
    png.write_bytes(b'png')
    monkeypatch.setattr(
        daemon.config_mod, 'load', lambda: {'max_file_size_mb': 1, 'info_panel': 'hidden'}
    )
    monkeypatch.setattr(daemon, 'folder_is_reachable', lambda path: True)

    def produce(target, config, identity):
        environment.check_size(identity, config)
        return png

    monkeypatch.setattr(daemon, 'produce', produce)
    request = tmp_path / 'token.pvqlreq'
    body = {'path': '/data/huge.vtu', 'mtime': 1, 'size': 5 * 2**30, 'force': True}
    request.write_text(json.dumps(body))
    daemon.handle(request)
    reply = json.loads((tmp_path / 'token.pvqlrep').read_text())
    assert reply['ok'] is True


def test_handle_sends_the_header_ahead_of_a_slow_preview(tmp_path, monkeypatch):
    """A large file's header arrives first, and the preview after it."""
    png = tmp_path / 'cached.png'
    png.write_bytes(b'png')
    early = tmp_path / f'token{daemon.EARLY_SUFFIX}'
    monkeypatch.setattr(daemon.config_mod, 'load', dict)
    monkeypatch.setattr(daemon, 'folder_is_reachable', lambda path: True)
    monkeypatch.setattr(daemon, 'cached_preview', lambda identity, config: None)
    monkeypatch.setattr(daemon.convert_mod, 'header', lambda source, config: {'type': 'Early'})
    monkeypatch.setattr(daemon.convert_mod, 'describe', lambda *args, **kwargs: {'type': 'Final'})

    def slow(target, config, identity):
        deadline = time.monotonic() + 10
        while not early.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        return png

    monkeypatch.setattr(daemon, 'produce', slow)
    request = tmp_path / 'token.pvqlreq'
    request.write_text(
        json.dumps({'path': '/data/big.vtu', 'mtime': 1, 'size': daemon.EARLY_BYTES})
    )
    daemon.handle(request)
    assert json.loads(early.read_text()) == {'ok': True, 'info': {'type': 'Early'}}
    assert json.loads((tmp_path / 'token.pvqlrep').read_text())['info'] == {'type': 'Final'}


def converting_together(tmp_path, monkeypatch, sizes, budget_mb):
    """Request files of the given sizes at once and return how many converted together."""
    monkeypatch.setattr(daemon.config_mod, 'load', lambda: {'max_conversion_memory_mb': budget_mb})
    monkeypatch.setattr(daemon, 'folder_is_reachable', lambda path: True)
    monkeypatch.setattr(daemon, 'cached_preview', lambda identity, config: None)
    monkeypatch.setattr(daemon.convert_mod, 'header', lambda source, config: None)
    monkeypatch.setattr(daemon.convert_mod, 'describe', lambda *args, **kwargs: None)
    png = tmp_path / 'built.png'
    png.write_bytes(b'png')
    running, peak = [], []
    together = threading.Barrier(len(sizes), timeout=1)

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
        request = tmp_path / f'r{index}.pvqlreq'
        request.write_text(json.dumps({'path': f'/data/{index}.vtu', 'mtime': 1, 'size': size}))
        threads.append(threading.Thread(target=daemon.handle, args=(request,), daemon=True))
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
    monkeypatch.setattr(daemon.config_mod, 'load', dict)
    monkeypatch.setattr(daemon, 'folder_is_reachable', lambda path: True)
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
            time.sleep(0.2)
            png.write_bytes(b'png')
        running.remove(target)
        return png

    monkeypatch.setattr(daemon, 'produce', build)
    threads = []
    for token in ('small', 'large'):
        request = tmp_path / f'{token}.pvqlreq'
        request.write_text(json.dumps({'path': '/data/a.vtu', 'mtime': 1, 'size': 10}))
        threads.append(threading.Thread(target=daemon.handle, args=(request,)))
        threads[-1].start()
    for thread in threads:
        thread.join(10)
    assert builds == ['/data/a.vtu']
    assert max(peak) == 1
    for token in ('small', 'large'):
        assert json.loads((tmp_path / f'{token}.pvqlrep').read_text())['ok'] is True
    assert daemon._previewing == {}


def test_scratch_files_are_named_per_thread():
    """Two builds in one process never write the same scratch file."""
    tags = []
    worker = threading.Thread(target=lambda: tags.append(cache.scratch_tag()))
    worker.start()
    worker.join(5)
    assert tags[0] != cache.scratch_tag()


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
    time.sleep(0.1)
    later = threading.Thread(target=run, args=('later', 1), daemon=True)
    later.start()
    time.sleep(0.1)
    assert order == ['small']
    release.set()
    for thread in (first, big, later):
        thread.join(5)
    assert order == ['small', 'big', 'later']


def test_a_handler_that_dies_still_replies(tmp_path, monkeypatch):
    """An error outside the handler's own guard is reported, not lost with the thread."""
    monkeypatch.setattr(daemon, 'handle', lambda request: 1 / 0)
    request = tmp_path / 'token.pvqltaken'
    request.write_text('{}')
    daemon.answer(request)
    reply = json.loads((tmp_path / 'token.pvqlrep').read_text())
    assert reply['ok'] is False
    assert reply['error'].startswith('ZeroDivisionError')
    assert not request.exists()


def test_a_handler_that_replied_before_dying_keeps_its_reply(tmp_path, monkeypatch):
    """A reply already written is not replaced by the error that followed it."""

    def reply_then_die(request):
        request.with_suffix(daemon.REPLY_SUFFIX).write_text('{"ok": true}')
        raise ZeroDivisionError

    monkeypatch.setattr(daemon, 'handle', reply_then_die)
    request = tmp_path / 'token.pvqltaken'
    request.write_text('{}')
    daemon.answer(request)
    assert json.loads((tmp_path / 'token.pvqlrep').read_text()) == {'ok': True}


@pytest.mark.parametrize('reachable', [True, False])
def test_a_large_file_previewed_once_is_not_refused_again(tmp_path, monkeypatch, reachable):
    """Once Preview Anyway has built a file, it is served again, even from a private folder."""
    png = tmp_path / 'cached.png'
    png.write_bytes(b'png')
    monkeypatch.setattr(daemon.config_mod, 'load', lambda: {'max_file_size_mb': 1})
    monkeypatch.setattr(daemon, 'folder_is_reachable', lambda path: reachable)
    monkeypatch.setattr(daemon, 'cached_preview', lambda identity, config: png)
    monkeypatch.setattr(daemon, 'produce', lambda *args: pytest.fail('built again'))
    monkeypatch.setattr(daemon.convert_mod, 'header', lambda *args: pytest.fail('header read'))
    monkeypatch.setattr(daemon.convert_mod, 'describe', lambda *args, **kwargs: {'type': 'X'})
    request = tmp_path / 'token.pvqlreq'
    request.write_text(json.dumps({'path': '/data/huge.vtu', 'mtime': 1, 'size': 5 * 10**9}))
    daemon.handle(request)
    reply = json.loads((tmp_path / 'token.pvqlrep').read_text())
    assert reply['ok'] is True
    assert Path(reply['png']).read_bytes() == b'png'


def test_the_memory_budget_and_estimates(tmp_path):
    """The budget is half the memory unless set, 0 lifts it, and a folder converts alone."""
    assert daemon.memory_budget({}) == daemon.physical_memory() // 2
    assert daemon.memory_budget({'max_conversion_memory_mb': 0}) == sys.maxsize
    assert daemon.memory_budget({'max_conversion_memory_mb': 512}) == 512_000_000
    assert daemon.conversion_memory('/data/a.vtu', ('/data/a.vtu', 1, 10)) == (
        daemon.BASE_MEMORY + 10 * daemon.MEMORY_PER_FILE_BYTE
    )
    assert daemon.conversion_memory(str(tmp_path), (str(tmp_path), 1, 10)) == sys.maxsize
    assert daemon.conversion_memory('/data/a.vtu', None) == sys.maxsize


def test_a_forced_preview_also_sends_its_header_first(tmp_path, monkeypatch):
    """Previewing a file above the limit anyway still shows its header while it converts."""
    png = tmp_path / 'cached.png'
    png.write_bytes(b'png')
    early = tmp_path / f'token{daemon.EARLY_SUFFIX}'
    monkeypatch.setattr(daemon.config_mod, 'load', lambda: {'max_file_size_mb': 1})
    monkeypatch.setattr(daemon, 'folder_is_reachable', lambda path: True)
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
    request = tmp_path / 'token.pvqlreq'
    body = {'path': '/data/huge.vtu', 'mtime': 1, 'size': 5_000_000_000, 'force': True}
    request.write_text(json.dumps(body))
    daemon.handle(request)
    assert json.loads(early.read_text())['info'] == {'type': 'Early'}
    assert json.loads((tmp_path / 'token.pvqlrep').read_text())['ok'] is True


def test_a_second_request_gets_its_header_while_the_first_converts(tmp_path, monkeypatch):
    """Quick Look asks twice for one file; the second waits for the mesh, not for the header."""
    png = tmp_path / 'cached.png'
    png.write_bytes(b'png')
    # Too little memory for two at once, so the second conversion waits for the first.
    monkeypatch.setattr(daemon.config_mod, 'load', lambda: {'max_conversion_memory_mb': 1})
    monkeypatch.setattr(daemon, 'folder_is_reachable', lambda path: True)
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
        request = tmp_path / f'{token}.pvqlreq'
        request.write_text(body)
        threads.append(threading.Thread(target=daemon.handle, args=(request,)))
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


def test_no_header_is_sent_after_the_preview(tmp_path, monkeypatch):
    """A header that comes back after the preview was sent is dropped."""
    png = tmp_path / 'cached.png'
    png.write_bytes(b'png')
    sent = threading.Event()
    monkeypatch.setattr(daemon.config_mod, 'load', dict)
    monkeypatch.setattr(daemon, 'folder_is_reachable', lambda path: True)
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

    request = tmp_path / 'token.pvqlreq'
    request.write_text(
        json.dumps({'path': '/data/big.vtu', 'mtime': 1, 'size': daemon.EARLY_BYTES})
    )
    daemon.handle(request)
    monkeypatch.setattr(daemon.convert_mod, 'header', lambda source, config: {'type': 'Late'})
    [(target, args)] = threads
    target(*args)
    assert sent.is_set()
    assert not (tmp_path / f'token{daemon.EARLY_SUFFIX}').exists()


def test_small_or_cached_files_get_no_early_header(tmp_path, monkeypatch):
    """Only a large file that is not yet cached is worth a header first."""
    png = tmp_path / 'cached.png'
    png.write_bytes(b'png')
    monkeypatch.setattr(daemon.config_mod, 'load', dict)
    monkeypatch.setattr(daemon, 'folder_is_reachable', lambda path: True)
    monkeypatch.setattr(daemon, 'produce', lambda target, config, identity: png)
    monkeypatch.setattr(daemon.convert_mod, 'describe', lambda *args, **kwargs: None)
    monkeypatch.setattr(daemon.convert_mod, 'header', lambda *args: pytest.fail('header read'))
    for size, is_cached in ((daemon.EARLY_BYTES - 1, False), (daemon.EARLY_BYTES, True)):
        monkeypatch.setattr(
            daemon, 'cached_preview', lambda identity, config, value=is_cached: value and png
        )
        request = tmp_path / 'token.pvqlreq'
        request.write_text(json.dumps({'path': '/data/big.vtu', 'mtime': 1, 'size': size}))
        daemon.handle(request)
        assert json.loads((tmp_path / 'token.pvqlrep').read_text())['ok'] is True


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


def test_a_cached_preview_does_not_wait_for_another_conversion(tmp_path, monkeypatch):
    """Only previews still to be built queue for the conversion lock."""
    png = tmp_path / 'cached.png'
    png.write_bytes(b'png')
    monkeypatch.setattr(daemon.config_mod, 'load', dict)
    monkeypatch.setattr(daemon, 'folder_is_reachable', lambda path: True)
    monkeypatch.setattr(daemon, 'cached_preview', lambda identity, config: png)
    monkeypatch.setattr(daemon, 'produce', lambda *args: pytest.fail('built again'))
    monkeypatch.setattr(daemon.convert_mod, 'describe', lambda *args, **kwargs: None)
    request = tmp_path / 'token.pvqlreq'
    request.write_text(json.dumps({'path': '/data/a.vtu', 'mtime': 1, 'size': 10}))
    with daemon.CONVERTING.reserve(1, 1):
        worker = threading.Thread(target=daemon.handle, args=(request,))
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

    def describe(*args, read=True):
        if read:
            held.append(daemon.CONVERTING.running)
            described.set()

    monkeypatch.setattr(daemon.config_mod, 'load', lambda: {'interactive': False})
    monkeypatch.setattr(daemon, 'folder_is_reachable', lambda path: True)
    monkeypatch.setattr(daemon, 'produce', lambda target, config, identity: png)
    monkeypatch.setattr(daemon.convert_mod, 'describe', describe)
    request = tmp_path / 'token.pvqlreq'
    request.write_text(json.dumps({'path': '/data/a.vtu', 'mtime': 1, 'size': 10}))
    daemon.handle(request)
    assert described.wait(5)
    assert held == [1]


@pytest.fixture
def installation(home, commands, monkeypatch):
    """Lay out a fake installation under tmp_path and record the commands run."""
    support = home / 'support'
    for folder in ('venv', 'src', 'unpacked'):
        (support / folder).mkdir(parents=True)
        (support / folder / 'file').write_text('x')
    (support / 'config.json').write_text('{}')
    app = home / 'Applications' / plist.APP_BUNDLE
    (app / 'Contents' / 'PlugIns').mkdir(parents=True)
    (home / 'cache').mkdir()
    (home / 'agent.plist').write_text('x')
    (home / 'container').mkdir()
    (home / 'pvqld.log').write_text('x')
    (home / 'uv').write_text('#!/bin/sh\n')
    monkeypatch.setattr(cli.shutil, 'which', lambda name: str(home / 'uv'))
    return home, commands


def test_uninstall_removes_everything_but_the_config(installation, capsys):
    """`pvql uninstall --yes` removes the installation and keeps the config file."""
    tmp_path, commands = installation
    assert cli.cmd_uninstall(argparse.Namespace(yes=True, all=False)) == 0
    for gone in (
        f'Applications/{plist.APP_BUNDLE}',
        'agent.plist',
        'cache',
        'container',
        'pvqld.log',
        'support/venv',
        'support/src',
        'support/unpacked',
    ):
        assert not (tmp_path / gone).exists(), gone
    assert (tmp_path / 'support' / 'config.json').exists()
    joined = [' '.join(str(part) for part in command) for command in commands]
    assert any('pluginkit -r' in c for c in joined)
    assert any('lsregister -u' in c for c in joined)
    assert any('launchctl bootout' in c for c in joined)
    assert any(c.endswith('uv tool uninstall pyvista-quicklook') for c in joined)
    assert 'keeps' in capsys.readouterr().out


def test_uninstall_removes_a_bundle_under_the_old_name(installation):
    """A bundle installed before the rename is found and removed as well."""
    tmp_path, _ = installation
    legacy = tmp_path / 'Applications' / plist.LEGACY_APP_BUNDLE
    (legacy / 'Contents' / 'PlugIns').mkdir(parents=True)
    assert legacy in cli.installed_apps()
    assert cli.cmd_uninstall(argparse.Namespace(yes=True, all=False)) == 0
    assert not legacy.exists()


def test_uninstall_removes_the_service_under_the_old_label(installation):
    """A service installed under the io.github.user27182 label is stopped and removed."""
    tmp_path, commands = installation
    (tmp_path / 'old-agent.plist').write_text('x')
    cli.daemon_mod.legacy_drop_dir().mkdir(parents=True)
    assert cli.cmd_uninstall(argparse.Namespace(yes=True, all=False)) == 0
    assert not (tmp_path / 'old-agent.plist').exists()
    assert not cli.daemon_mod.legacy_drop_dir().exists()
    joined = [' '.join(str(part) for part in command) for command in commands]
    assert any('bootout' in c and c.endswith(f'/{daemon.LEGACY_LABEL}') for c in joined)


def test_uninstall_all_removes_the_config_too(installation):
    """`pvql uninstall --all` removes the config file as well."""
    tmp_path, _ = installation
    assert cli.cmd_uninstall(argparse.Namespace(yes=True, all=True)) == 0
    assert not (tmp_path / 'support' / 'config.json').exists()


def test_uninstall_only_reports_without_a_terminal(installation, capsys):
    """Without --yes and without a terminal to ask on, nothing is removed."""
    tmp_path, commands = installation
    assert cli.cmd_uninstall(argparse.Namespace(yes=False, all=False)) == 1
    assert (tmp_path / 'Applications' / plist.APP_BUNDLE).exists()
    assert commands == []
    out = capsys.readouterr().out
    assert 'pvql uninstall --yes' in out
    # The config file is kept, so the flag that takes it too is named separately.
    assert '--all' in out


def test_uv_note_explains_how_to_remove_an_installed_uv(tmp_path, monkeypatch):
    """Uv the installer fetched is explained; uv from elsewhere is left to its owner."""
    monkeypatch.setattr(cli.Path, 'home', classmethod(lambda cls: tmp_path))
    ours = cli.uv_note(tmp_path / '.local' / 'bin' / 'uv')
    assert 'uv cache clean' in ours
    assert 'not only this one' in ours
    assert 'whatever installed it' in cli.uv_note(Path('/opt/homebrew/bin/uv'))


def test_agent_plist_runs_the_daemon():
    """The launch agent starts the render service and keeps it running."""
    agent = daemon.agent_plist('/usr/local/bin/pvql')
    assert agent['ProgramArguments'] == ['/usr/local/bin/pvql', 'daemon']
    assert agent['KeepAlive'] is True
    assert agent['Label'] == daemon.LABEL
    assert agent['AssociatedBundleIdentifiers'] == [plist.APP_BUNDLE_ID]


def test_service_install_runs_the_daemon_under_the_package_name(tmp_path, monkeypatch):
    """The agent runs the pyvista-quicklook entry point, which is the name macOS shows."""
    for name in ('pvql', 'pyvista-quicklook'):
        (tmp_path / name).write_text('#!/bin/sh\n')
        (tmp_path / name).chmod(0o755)
    agent = tmp_path / 'agent.plist'
    old_agent = tmp_path / 'old-agent.plist'
    old_agent.write_text('x')
    old_drop = tmp_path / 'old-container'
    old_drop.mkdir()
    monkeypatch.setattr(cli.daemon_mod, 'agent_path', lambda: agent)
    monkeypatch.setattr(cli.daemon_mod, 'legacy_agent_path', lambda: old_agent)
    monkeypatch.setattr(cli.daemon_mod, 'legacy_drop_dir', lambda: old_drop)
    commands = []
    monkeypatch.setattr(
        cli.subprocess,
        'run',
        lambda command, **k: (
            commands.append(command) or subprocess.CompletedProcess(command, 0, '', '')
        ),
    )
    args = argparse.Namespace(install=True, uninstall=False, helper=str(tmp_path / 'pvql'))
    assert cli.cmd_service(args) == 0
    stored = plistlib.loads(agent.read_bytes())
    assert stored['ProgramArguments'] == [str(tmp_path / 'pyvista-quicklook'), 'daemon']
    assert not old_agent.exists()
    assert not old_drop.exists()
    joined = [' '.join(str(part) for part in command) for command in commands]
    assert any('bootout' in c and c.endswith(f'/{daemon.LEGACY_LABEL}') for c in joined)


def test_parser_accepts_every_subcommand():
    """Each documented subcommand parses."""
    parser = cli.build_parser()
    for argv in (
        ['preview', 'mesh.vtu'],
        ['warm', 'dir'],
        ['types', '--all'],
        ['plist', '--app', 'a.plist', '--extension', 'e.plist'],
        ['config', '--init'],
        ['cache', '--clear'],
        ['finder'],
        ['doctor'],
        ['daemon'],
        ['service', '--install'],
        ['uninstall', '--yes', '--all'],
    ):
        assert parser.parse_args(argv).func is not None


def test_claimed_extensions_follows_config():
    """The claimed set reflects the config additions and removals."""
    claimed = cli.claimed_extensions({'extensions': {'add': ['.msh'], 'remove': ['.vtu']}})
    assert '.msh' in claimed
    assert '.vtu' not in claimed


def test_types_lists_claimed_extensions(capsys, monkeypatch):
    """`pvql types` prints one line per claimed extension, and marks macOS's own apart."""
    monkeypatch.setattr(cli.config_mod, 'load', lambda: dict(cli.config_mod.DEFAULTS))
    assert cli.cmd_types(argparse.Namespace(all=False)) == 0
    lines = capsys.readouterr().out.splitlines()
    assert any(line.startswith('✓ .vtu ') for line in lines)
    [stl] = [line for line in lines if ' .stl ' in f' {line}']
    assert stl.startswith('→ .stl ')
    assert 'not previewed' in stl
    assert any('extensions claimed' in line for line in lines)


def test_plist_writes_every_file(tmp_path, monkeypatch):
    """`pvql plist` writes the app's and both extensions' property lists."""
    monkeypatch.setattr(cli.config_mod, 'load', lambda: dict(cli.config_mod.DEFAULTS))
    app = tmp_path / 'App.plist'
    ext = tmp_path / 'Ext.plist'
    thumb = tmp_path / 'Thumb.plist'
    args = argparse.Namespace(
        app=str(app), extension=str(ext), thumbnail=str(thumb), helper='/bin/pvql'
    )
    assert cli.cmd_plist(args) == 0
    assert plistlib.loads(app.read_bytes())['PVQLHelperPath'] == '/bin/pvql'
    assert plistlib.loads(ext.read_bytes())['CFBundlePackageType'] == 'XPC!'
    assert plistlib.loads(thumb.read_bytes())['CFBundleIdentifier'] == plist.THUMB_BUNDLE_ID


def test_preview_reports_failures_on_stderr(capsys, monkeypatch):
    """A render failure exits non-zero and explains itself."""

    def fail(path, config):
        message = 'nope'
        raise environment.RenderError(message)

    monkeypatch.setattr(cli.config_mod, 'load', lambda: dict(cli.config_mod.DEFAULTS))
    monkeypatch.setattr(cli.daemon_mod, 'produce', fail)
    args = argparse.Namespace(path='mesh.vtu', output=None, no_cache=True)
    assert cli.cmd_preview(args) == 1
    assert 'nope' in capsys.readouterr().err


def test_preview_prints_the_cached_path(capsys, monkeypatch, tmp_path):
    """A successful build prints where the scene landed."""
    ply = tmp_path / 'out.ply'
    ply.write_bytes(b'x')
    monkeypatch.setattr(cli.config_mod, 'load', lambda: dict(cli.config_mod.DEFAULTS))
    monkeypatch.setattr(cli.daemon_mod, 'produce', lambda path, config: ply)
    args = argparse.Namespace(path='mesh.vtu', output=None, no_cache=False)
    assert cli.cmd_preview(args) == 0
    assert str(ply) in capsys.readouterr().out


def test_warm_builds_every_claimed_file_in_a_directory(capsys, monkeypatch, tmp_path):
    """`pvql warm DIR` builds the claimed files it finds and reports the failures."""
    (tmp_path / 'good.vtu').write_text('x')
    (tmp_path / 'bad.vtp').write_text('x')
    (tmp_path / 'notes.txt').write_text('x')
    built = []

    def produce(target, config):
        if target.suffix == '.vtp':
            message = 'unreadable'
            raise environment.RenderError(message)
        built.append(target.name)
        return target

    monkeypatch.setattr(cli.config_mod, 'load', lambda: dict(cli.config_mod.DEFAULTS))
    monkeypatch.setattr(cli.daemon_mod, 'produce', produce)
    assert cli.cmd_warm(argparse.Namespace(paths=[str(tmp_path)])) == 1
    assert built == ['good.vtu']
    captured = capsys.readouterr()
    assert '1 of 2 cached' in captured.out
    assert 'bad.vtp: unreadable' in captured.err


def test_cache_reports_its_size(capsys, monkeypatch, tmp_path):
    """`pvql cache` counts previews, not the descriptions kept beside them."""
    (tmp_path / 'a.png').write_bytes(b'0123456789')
    (tmp_path / 'v-a.json').write_bytes(b'{}')
    monkeypatch.setattr(cli.config_mod, 'CACHE_DIR', tmp_path)
    assert cli.cmd_cache(argparse.Namespace(clear=False)) == 0
    assert '1 previews' in capsys.readouterr().out


def test_cache_clear_removes_previews_but_not_the_stamp(capsys, monkeypatch, tmp_path):
    """`pvql cache --clear` deletes the previews and leaves the warm-up stamp."""
    (tmp_path / 'a.png').write_bytes(b'x')
    (tmp_path / 'b.ply').write_bytes(b'x')
    (tmp_path / 'b.json').write_bytes(b'{}')
    (tmp_path / '.warm').touch()
    monkeypatch.setattr(cli.config_mod, 'CACHE_DIR', tmp_path)
    assert cli.cmd_cache(argparse.Namespace(clear=True)) == 0
    assert 'removed 2 cached previews' in capsys.readouterr().out
    assert [p.name for p in tmp_path.iterdir()] == ['.warm']


def test_config_load_merges_over_defaults(tmp_path, monkeypatch):
    """Unknown keys are dropped and missing ones fall back to defaults."""
    path = tmp_path / 'config.json'
    path.write_text(json.dumps({'timeout': 5, 'bogus': 1}))
    monkeypatch.setattr(config, 'CONFIG_PATH', path)
    loaded = config.load()
    assert loaded['timeout'] == 5
    assert 'bogus' not in loaded
    assert loaded['window_size'] == config.DEFAULTS['window_size']


def test_config_load_survives_broken_json(tmp_path, monkeypatch):
    """A corrupt config file falls back to defaults instead of raising."""
    path = tmp_path / 'config.json'
    path.write_text('{not json')
    monkeypatch.setattr(config, 'CONFIG_PATH', path)
    assert config.load()['timeout'] == config.DEFAULTS['timeout']


def test_executable_rejects_what_cannot_run(tmp_path):
    """Missing, unset, and non-executable paths are all reported as not found."""
    assert config.executable(None) is None
    assert config.executable(str(tmp_path / 'absent')) is None
    plain = tmp_path / 'plain'
    plain.write_text('data')
    assert config.executable(str(plain)) is None


def test_executable_accepts_a_runnable_file(tmp_path):
    """An executable file is returned as given."""
    tool = tmp_path / 'pyvista'
    tool.write_text('#!/bin/sh\n')
    tool.chmod(0o755)
    assert config.executable(str(tool)) == str(tool)


def test_bundle_version_is_numeric():
    """Bundle versions carry only the leading numeric part."""
    assert plist.bundle_version('1.2.3.dev4+gabc') == '1.2.3'
    assert plist.bundle_version('nonsense') == '0.0.0'


def test_scene_key_differs_from_the_image_key():
    """A file's scene and its rendered image are cached separately."""
    identity = ('/mesh.vtu', 1, 2)
    settings = {'window_size': [8, 8], 'max_scene_points': 100, 'max_glyph_points': 20}
    assert convert.scene_key(identity, settings) != render.cache_key(identity, settings)


def test_scene_key_follows_conversion_settings():
    """Changing either point budget invalidates the scene."""
    identity = ('/mesh.vtu', 1, 2)
    base = {'max_scene_points': 100, 'max_glyph_points': 20}
    assert convert.scene_key(identity, base) != convert.scene_key(
        identity, {**base, 'max_scene_points': 200}
    )
    assert convert.scene_key(identity, base) != convert.scene_key(
        identity, {**base, 'max_glyph_points': 40}
    )


def test_scene_passes_a_zero_glyph_budget_through(tmp_path, monkeypatch):
    """A glyph budget of zero means no cap, and must not fall back to the default."""
    sample = tmp_path / 'mesh.vtu'
    sample.write_text('x')
    seen = {}

    def fake_run(config, command, **kwargs):
        seen['command'] = command
        Path(command[3]).write_bytes(b'ply')

    monkeypatch.setattr(convert.environment, 'run', fake_run)
    monkeypatch.setattr(convert.config_mod, 'CACHE_DIR', tmp_path / 'cache')
    config = {'python': sys.executable, 'max_glyph_points': 0, 'cache': False}
    out = convert.scene(sample, config)
    command = seen['command']
    assert command[command.index('--max-glyphs') + 1] == '0'
    assert command[command.index('--max-points') + 1] == str(config_defaults()['max_scene_points'])
    assert out.read_bytes() == b'ply'


def config_defaults():
    """Return a copy of the built-in configuration."""
    return dict(config.DEFAULTS)


def test_scene_version_follows_the_exporter(tmp_path):
    """Any change to the exporter changes the version scenes are keyed and named by."""
    exporter = tmp_path / 'exporter.py'
    exporter.write_text('a')
    before = convert.converter_version(exporter)
    exporter.write_text('b')
    assert convert.converter_version(exporter) != before
    assert convert.converter_version() == convert.SCENE_VERSION


def test_scenes_of_other_versions_are_discarded(tmp_path, monkeypatch):
    """An older exporter's scenes and descriptions go; current ones and images stay."""
    monkeypatch.setattr(cache.config_mod, 'CACHE_DIR', tmp_path)
    current = convert.SCENE_VERSION
    names = ('old-1.ply', 'old-1.json', f'{current}-2.ply', f'{current}-2.json', 'image.png')
    for name in names:
        (tmp_path / name).write_bytes(b'x')
    assert cache.discard_other_versions(current) == 2
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        f'{current}-2.json',
        f'{current}-2.ply',
        'image.png',
    ]


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
    config = {'max_file_size_mb': 1, 'python': str(tmp_path / 'absent')}
    with pytest.raises(environment.RenderError, match='preview limit'):
        convert.scene(sample, config, identity=(str(sample), 0, 5 * 1024 * 1024))


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
        raise environment.RenderError(message)

    monkeypatch.setattr(daemon.convert_mod, 'scene', fail)
    monkeypatch.setattr(daemon.render_mod, 'preview', lambda *a, **k: pytest.fail('rendered'))
    with pytest.raises(environment.RenderError, match='no surface'):
        daemon.produce('/mesh.vtu', {'interactive': True})


def test_handle_delivers_an_interactive_scene(tmp_path, monkeypatch):
    """When a scene is built, the reply points at it rather than an image."""
    ply = tmp_path / 'scene.ply'
    ply.write_bytes(b'ply\n')
    monkeypatch.setattr(daemon, 'folder_is_reachable', lambda path: True)
    monkeypatch.setattr(daemon.convert_mod, 'scene', lambda target, config, identity=None: ply)

    request = tmp_path / 'token.pvqlreq'
    request.write_text(json.dumps({'path': str(tmp_path / 'mesh.vtu'), 'mtime': 1, 'size': 2}))
    daemon.handle(request)

    reply = json.loads((tmp_path / 'token.pvqlrep').read_text())
    assert reply['ok'] is True
    assert reply['scene'].endswith('token.ply')
    assert (tmp_path / 'token.ply').read_bytes() == b'ply\n'


def test_find_python_prefers_the_configured_interpreter(tmp_path):
    """An explicit interpreter is used as given."""
    tool = tmp_path / 'python'
    tool.write_text('#!/bin/sh\n')
    tool.chmod(0o755)
    assert config.find_python({'python': str(tool)}) == str(tool)


def test_find_python_reports_nothing_without_an_environment():
    """No interpreter and no CLI means no environment, whatever PATH holds."""
    assert config.find_python({}) is None
    assert config.resolve_pyvista({}) is None


def test_warmup_script_ships_with_the_package():
    """The warm-up script the PyVista interpreter runs is part of the package."""
    assert warmup.WARMER.is_file()


def test_warm_reports_a_missing_interpreter(monkeypatch):
    """Warming without a usable PyVista environment explains what to set."""
    monkeypatch.setattr(warmup.config_mod, 'find_python', lambda configured=None: None)
    with pytest.raises(environment.RenderError, match='interpreter'):
        warmup.warm({})


def test_warm_in_background_survives_a_failing_warm_up(monkeypatch):
    """A failing warm-up never brings the service down."""
    calls = []

    def explode(config):
        calls.append(config)
        message = 'no interpreter'
        raise environment.RenderError(message)

    monkeypatch.setattr(daemon.warmup_mod, 'warmed_recently', lambda: False)
    monkeypatch.setattr(daemon.warmup_mod, 'warm', explode)
    thread = daemon.warm_in_background({})
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert calls == [{}]


def test_warmed_recently_reads_the_stamp(tmp_path, monkeypatch):
    """A fresh stamp counts as warm; a missing one does not."""
    monkeypatch.setattr(warmup.config_mod, 'CACHE_DIR', tmp_path)
    assert warmup.warmed_recently() is False
    warmup.stamp_path().touch()
    assert warmup.warmed_recently() is True
    assert warmup.warmed_recently(within=0) is False


def test_warm_in_background_skips_a_recent_warm_up(monkeypatch):
    """The service does not compete with a warm-up the installer just ran."""
    called = []
    monkeypatch.setattr(daemon.warmup_mod, 'warmed_recently', lambda: True)
    monkeypatch.setattr(daemon.warmup_mod, 'warm', lambda config: called.append(config))
    daemon.warm_in_background({}).join(timeout=5)
    assert called == []


def test_overrides_drops_settings_that_match_the_defaults():
    """Only deliberate changes are stored, so defaults stay free to improve."""
    merged = {**config.DEFAULTS, 'python': '/bin/python', 'timeout': 5}
    stored = config.overrides(merged)
    assert stored == {'python': '/bin/python', 'timeout': 5}
    assert 'max_scene_points' not in stored


def test_overrides_round_trips_through_load(tmp_path, monkeypatch):
    """A stored override survives, and everything else follows the defaults."""
    path = tmp_path / 'config.json'
    monkeypatch.setattr(config, 'CONFIG_PATH', path)
    path.write_text(json.dumps(config.overrides({**config.DEFAULTS, 'timeout': 5})))
    loaded = config.load()
    assert loaded['timeout'] == 5
    assert loaded['max_scene_points'] == config.DEFAULTS['max_scene_points']


def test_handle_reports_why_the_scene_failed(tmp_path, monkeypatch):
    """The scene's own error is what gets reported."""

    def no_scene(target, config, identity=None):
        message = 'the dataset has no surface to show'
        raise environment.RenderError(message)

    monkeypatch.setattr(daemon, 'folder_is_reachable', lambda path: True)
    monkeypatch.setattr(daemon.convert_mod, 'scene', no_scene)
    request = tmp_path / 'token.pvqlreq'
    request.write_text(json.dumps({'path': str(tmp_path / 'mesh.vtu'), 'mtime': 1, 'size': 2}))
    daemon.handle(request)
    reply = json.loads((tmp_path / 'token.pvqlrep').read_text())
    assert reply['ok'] is False
    assert 'no surface' in reply['error']


def test_config_init_with_an_interpreter_clears_a_stale_cli(tmp_path, monkeypatch):
    """An environment named by interpreter does not keep an unrelated CLI on record."""
    path = tmp_path / 'config.json'
    monkeypatch.setattr(config, 'CONFIG_PATH', path)
    monkeypatch.setattr(config, 'APP_SUPPORT', tmp_path)
    path.write_text(json.dumps({'pyvista': '/old/bin/pyvista'}))
    args = argparse.Namespace(
        init=True, python='/new/bin/python', pyvista=None, helper='/bin/pvql'
    )
    cli.cmd_config(args)
    stored = json.loads(path.read_text())
    assert stored['python'] == '/new/bin/python'
    assert 'pyvista' not in stored


def test_resolve_pyvista_stays_inside_the_configured_environment(tmp_path):
    """A configured interpreter without a sibling CLI does not fall back to PATH."""
    interpreter = tmp_path / 'python'
    interpreter.write_text('#!/bin/sh\n')
    interpreter.chmod(0o755)
    assert config.resolve_pyvista({'python': str(interpreter)}) is None

    sibling = tmp_path / 'pyvista'
    sibling.write_text('#!/bin/sh\n')
    sibling.chmod(0o755)
    assert config.resolve_pyvista({'python': str(interpreter)}) == str(sibling)


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


def test_stale_scratch_files_are_discarded(monkeypatch, tmp_path):
    """Scratch left by a stopped build goes once it is old; a fresh one may still be in use."""
    monkeypatch.setattr(cache.config_mod, 'CACHE_DIR', tmp_path)
    old, fresh = tmp_path / 'a.1.2.tmp.json', tmp_path / 'b.1.2.tmp.ply'
    for path in (old, fresh, tmp_path / 'kept.json'):
        path.write_text('x')
    stamp = time.time() - 700
    os.utime(old, (stamp, stamp))
    assert cache.discard_scratch(600) == 1
    assert sorted(p.name for p in tmp_path.iterdir()) == ['b.1.2.tmp.ply', 'kept.json']


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
        while not budget.waiting:
            time.sleep(0.01)
        sooner = threading.Thread(target=wait, args=('preview', False))
        sooner.start()
        while len(budget.waiting) < 2:
            time.sleep(0.01)
    later.join(5)
    sooner.join(5)
    assert order == ['preview', 'description']


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
