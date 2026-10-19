"""End-to-end tests that run the exporter and preload under a real PyVista interpreter."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import threading
import time

import numpy as np
import pytest
import pyvista as pv

from pyvista_quicklook import cache
from pyvista_quicklook import config
from pyvista_quicklook import convert
from pyvista_quicklook import daemon
from pyvista_quicklook import environment
from pyvista_quicklook import preload
from pyvista_quicklook import render


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Return a configuration naming the test interpreter, with a cache of its own."""
    monkeypatch.setattr(config, 'CACHE_DIR', tmp_path / 'cache')
    return {**config.DEFAULTS, 'python': sys.executable}


def test_scene_is_built_by_the_exporter_and_cached(tmp_path, env, monkeypatch):
    """The exporter runs under the configured interpreter and its output is kept."""
    source = tmp_path / 'sphere.vtp'
    pv.Sphere().save(source)
    out = convert.scene(source, env)
    assert out.parent == config.CACHE_DIR
    assert out.name.startswith(f'{convert.SCENE_VERSION}-')
    assert pv.read(out).n_faces > 0
    assert not list(config.CACHE_DIR.glob('*.tmp*'))
    # The scene and the description read with it each record the file, so pruning keeps them.
    identity = cache.identity_of(source.resolve())
    for entry in (out, convert.info_path(identity)):
        record = json.loads(cache.record_path(entry).read_text())
        assert (record['path'], record['mtime'], record['size']) == identity
    assert cache.prune() == 0

    def never(*args, **kwargs):
        pytest.fail('a cached scene must not be built again')

    monkeypatch.setattr(convert.environment, 'run', never)
    assert convert.scene(source, env) == out


@pytest.mark.network
def test_a_folder_of_slices_is_converted(tmp_path, env):
    """A DICOM series arrives as a folder, and the whole folder is what gets read."""
    out = convert.scene(pv.examples.download_dicom_stack(load=False), env)
    assert pv.read(out).n_faces > 0


def test_scene_reports_what_the_exporter_said(tmp_path, env):
    """A file the exporter cannot handle is reported, and nothing is left in the cache."""
    source = tmp_path / 'broken.vtp'
    source.write_text('not a mesh')
    with pytest.raises(environment.RenderError, match=r'Converting broken\.vtp failed'):
        convert.scene(source, env)
    assert not list(config.CACHE_DIR.glob('*'))


def test_handle_answers_with_a_scene_for_a_real_file(tmp_path, env, monkeypatch):
    """A request for a readable file is answered with a scene the extension can load."""
    monkeypatch.setattr(daemon.config_mod, 'load', lambda: env)
    source = tmp_path / 'sphere.vtp'
    pv.Sphere().save(source)
    request = tmp_path / 'token.pvqlreq'
    request.write_text(json.dumps({'path': str(source), 'mtime': 1, 'size': 2, 'scene': True}))
    daemon.handle(request)
    reply = json.loads((tmp_path / 'token.pvqlrep').read_text())
    assert reply['ok'] is True
    assert pv.read(reply['scene']).n_points > 0
    assert reply['info']['type'] == 'PolyData'
    assert reply['info']['array_count'] == 1


def test_a_scene_cached_without_its_description_is_described(tmp_path, env):
    """A scene built while the panel was hidden gets its description when the panel returns."""
    source = tmp_path / 'sphere.vtp'
    pv.Sphere().save(source)
    hidden = {**env, 'info_panel': 'hidden'}
    convert.scene(source, hidden)
    identity = cache.identity_of(source.resolve())
    assert not convert.info_path(identity).exists()
    assert convert.describe(source, hidden) is None
    assert convert.describe(source, env)['type'] == 'PolyData'
    assert convert.info_path(identity).is_file()
    assert cache.record_path(convert.info_path(identity)).is_file()
    assert not list(config.CACHE_DIR.glob('*.tmp*'))


def test_only_a_cached_description_is_given_without_reading(tmp_path, env, monkeypatch):
    """Asked not to read, describe returns what is cached and starts nothing."""
    source = tmp_path / 'sphere.vtp'
    pv.Sphere().save(source)
    with monkeypatch.context() as patch:
        patch.setattr(environment, 'run', lambda *args, **kwargs: pytest.fail('read'))
        assert convert.describe(source, env, read=False) is None
    assert convert.describe(source, env)['type'] == 'PolyData'
    assert convert.describe(source, env, read=False)['type'] == 'PolyData'


def test_a_missing_file_or_environment_is_not_remembered(tmp_path, env):
    """Nothing is cached when the file is gone or no environment is set up, as both may change."""
    assert convert.describe(tmp_path / 'gone.vtp', env) is None
    source = tmp_path / 'sphere.vtp'
    pv.Sphere().save(source)
    assert convert.describe(source, {**env, 'python': '/no/such/python'}) is None
    assert not list(config.CACHE_DIR.glob('*.json'))


def test_preload_runs_the_script_and_leaves_a_stamp(env):
    """Preloading loads PyVista in the configured interpreter and records when it finished."""
    assert not preload.preloaded_recently()
    assert preload.run(env) > 0
    assert preload.preloaded_recently()


@pytest.mark.skipif(sys.platform != 'darwin', reason='rendering needs a display server on Linux')
def test_still_images_come_from_the_pyvista_command(tmp_path, env, monkeypatch):
    """The environment renders a still image with ``pyvista plot``, on the background asked for."""
    source = tmp_path / 'sphere.vtp'
    pv.Sphere().save(source)
    png = render.preview(source, {**env, 'window_size': [64, 64], 'background': 'white'})
    drawn = pixels(png)
    assert drawn.shape[:2] == (64, 64)
    assert (drawn[0, 0, :3] == 255).all()
    assert (
        daemon.produce(
            source, {**env, 'interactive': False, 'window_size': [64, 64], 'background': 'white'}
        )
        == png
    )
    assert convert.describe(source, env)['type'] == 'PolyData'
    # The description belongs to the file, so other render settings do not read it again.
    monkeypatch.setattr(environment, 'run', lambda *args, **kwargs: pytest.fail('read again'))
    assert convert.describe(source, {**env, 'window_size': [32, 32]})['type'] == 'PolyData'


def pixels(png):
    """Return a PNG's pixels as rows, top row first."""
    picture = pv.read(png)
    width, height = picture.dimensions[:2]
    return np.flipud(np.asarray(picture.active_scalars).reshape(height, width, -1))


@pytest.mark.skipif(sys.platform != 'darwin', reason='rendering needs a display server on Linux')
def test_a_saved_theme_colours_the_scene(tmp_path, env):
    """A theme saved as JSON reaches the exporter."""
    theme = pv.themes.DocumentTheme()
    theme.color = 'red'
    theme.save(tmp_path / 'theme.json')
    settings = {**env, 'theme': str(tmp_path / 'theme.json')}
    source = tmp_path / 'sphere.vtp'
    pv.Sphere().save(source)
    assert (pv.read(convert.scene(source, settings))['RGB'] == (255, 0, 0)).all()


@pytest.mark.skipif(sys.platform != 'darwin', reason='rendering needs a display server on Linux')
def test_a_named_theme_reaches_pyvista_plot(tmp_path, env):
    """``pyvista plot`` draws with the theme it is given by name, so the dark one looks dark."""
    source = tmp_path / 'sphere.vtp'
    pv.Sphere().save(source)
    plain = pixels(render.preview(source, {**env, 'window_size': [32, 32]}))
    dark = pixels(render.preview(source, {**env, 'window_size': [32, 32], 'theme': 'dark'}))
    assert plain.shape == dark.shape == (32, 32, 3)
    assert plain[0, 0, :3].mean() > dark[0, 0, :3].mean()
    assert not np.array_equal(plain, dark)


def test_header_is_read_by_the_exporter_without_the_values(tmp_path, env):
    """The service asks the exporter what a header says, which needs no conversion."""
    import pyvista_zstd

    source = tmp_path / 'sphere.pv'
    pyvista_zstd.write(pv.Sphere(), source)
    info = convert.header(source, env)
    assert info['type'] == 'PolyData'
    assert convert.header(source, {**env, 'info_panel': 'hidden'}) is None
    assert convert.header(source, {**env, 'python': str(tmp_path / 'missing')}) is None
    assert not list(config.CACHE_DIR.glob('*.tmp*'))


def test_a_reader_kept_ready_reads_the_next_header(tmp_path, env, monkeypatch):
    """A header goes to the reader started ahead, and the next one is started behind it."""
    import pyvista_zstd

    source = tmp_path / 'sphere.pv'
    pyvista_zstd.write(pv.Sphere(), source)
    expected = convert.header(source, env)
    convert.keep_reader_ready(env)
    waiting = convert._reader
    assert waiting is not None
    monkeypatch.setattr(environment, 'run', lambda *args, **kwargs: pytest.fail('ran'))
    assert convert.header(source, env) == expected
    assert waiting.process.returncode == 0
    assert convert._reader is not None
    assert convert._reader is not waiting
    assert not list(config.CACHE_DIR.glob('*.tmp*'))


def test_a_reader_that_cannot_serve_is_set_aside(tmp_path, env):
    """A reader for another interpreter, or whose read fails, gives no header and leaves none."""
    import pyvista_zstd

    source = tmp_path / 'sphere.pv'
    pyvista_zstd.write(pv.Sphere(), source)
    convert.keep_reader_ready(env)
    other = convert._reader
    # A reader for another interpreter is stopped, and the file is read without it.
    other.python = 'elsewhere'
    assert convert.header(source, env)['type'] == 'PolyData'
    assert other.process.returncode is not None
    assert not other.scratch.exists()
    # A read that fails is reported as no header.
    assert convert.header(tmp_path / 'missing' / 'gone.pv', env) is None
    failing = convert.take_reader(env['python'])
    failing.process.kill()
    with pytest.raises(environment.RenderError, match='Reading the header failed'):
        failing.read('', env)
    failing.stop()
    assert not list(config.CACHE_DIR.glob('*.tmp*'))


def test_only_formats_with_a_header_reader_get_a_header(tmp_path, env, monkeypatch):
    """A file with no header to read starts no interpreter, and an unreadable one gives nothing."""
    stl = tmp_path / 'part.stl'
    pv.Sphere().save(stl)
    broken = tmp_path / 'broken.vtu'
    broken.write_text('<VTKFile type="Nonsense"></VTKFile>')
    assert convert.header(broken, env) is None
    monkeypatch.setattr(environment, 'run', lambda *args, **kwargs: pytest.fail('ran'))
    assert convert.header(stl, env) is None


def test_a_scene_built_without_the_cache_keeps_its_description(tmp_path, env, monkeypatch):
    """The description written with a scene is used, rather than reading the file again."""
    source = tmp_path / 'sphere.vtp'
    pv.Sphere().save(source)
    settings = {**env, 'cache': False}
    convert.scene(source, settings)
    monkeypatch.setattr(environment, 'run', lambda *args, **kwargs: pytest.fail('read again'))
    assert convert.describe(source, settings)['type'] == 'PolyData'


def test_a_file_the_describer_cannot_describe_is_not_read_again(tmp_path, env, monkeypatch):
    """A describer that finishes without a description is remembered, so the file is read once."""
    source = tmp_path / 'sphere.vtp'
    pv.Sphere().save(source)
    monkeypatch.setattr(environment, 'run', lambda *args, **kwargs: None)
    assert convert.describe(source, env) is None
    monkeypatch.setattr(environment, 'run', lambda *args, **kwargs: pytest.fail('read again'))
    assert convert.describe(source, env) is None
    assert [json.loads(p.read_text()) for p in config.CACHE_DIR.glob('*.json')] == [{}]


def test_a_failed_read_is_tried_again(tmp_path, env, monkeypatch):
    """A read that failed or timed out may pass next time, so nothing is cached for it."""
    source = tmp_path / 'sphere.vtp'
    pv.Sphere().save(source)
    reads = []

    def fail(config, command, **kwargs):
        reads.append(kwargs.get('timeout'))
        # Output written before a failure is not trusted.
        Path(command[-1]).write_text('{"type": "PolyData"}')
        message = 'Describing timed out after 5 s.'
        raise environment.RenderError(message)

    monkeypatch.setattr(environment, 'run', fail)
    assert convert.describe(source, env, timeout=5) is None
    assert convert.describe(source, env) is None
    assert reads == [5, None]
    assert not list(config.CACHE_DIR.glob('*.json'))


def test_a_file_without_a_mesh_is_told_apart_from_a_failure(tmp_path, env):
    """The exporter's status says the file holds no mesh, and any other failure stays one."""
    from pyvista_quicklook import _scene_export

    assert environment.NOT_A_MESH_STATUS == _scene_export.NOT_A_MESH_STATUS
    notes = tmp_path / 'notes.vtu'
    notes.write_text('a table of numbers\n1 2 3\n')
    with pytest.raises(environment.NotAMeshError):
        convert.scene(notes, env)
    with pytest.raises(environment.RenderError) as raised:
        environment.run(env, [sys.executable, '-c', 'raise SystemExit(1)'], task='Failing')
    assert not isinstance(raised.value, environment.NotAMeshError)


class StopLoopError(Exception):
    """Raised to end the service loop in a test."""


def test_the_service_answers_a_request_end_to_end(tmp_path, env, monkeypatch):
    """A running service picks up a request, builds the scene, and the caller gets it back."""
    drop = tmp_path / 'container'
    monkeypatch.setattr(daemon, 'drop_dir', lambda: drop)
    monkeypatch.setattr(daemon, 'prune_in_background', lambda: None)
    monkeypatch.setattr(daemon.config_mod, 'load', lambda: {**env, 'preload_on_start': False})
    source = tmp_path / 'sphere.vtp'
    pv.Sphere().save(source)
    sleep, stop, outcome = time.sleep, threading.Event(), []

    def sleep_until_stopped(seconds):
        if stop.is_set() and threading.current_thread() is server:
            raise StopLoopError
        sleep(seconds)

    def serve():
        try:
            daemon.serve(poll=0.01)
        except StopLoopError:
            outcome.append('stopped')

    monkeypatch.setattr(daemon.time, 'sleep', sleep_until_stopped)
    server = threading.Thread(target=serve, daemon=True)
    server.start()
    try:
        scene = daemon.request_preview(str(source), timeout=120)
    finally:
        stop.set()
        server.join(10)
    assert outcome == ['stopped']
    assert scene.parent == drop
    assert pv.read(scene).n_faces > 0
    assert not list(drop.glob(f'*{daemon.REQUEST_SUFFIX}'))
    assert not list(drop.glob(f'*{daemon.TAKEN_SUFFIX}'))
