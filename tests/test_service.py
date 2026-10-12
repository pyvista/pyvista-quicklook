"""End-to-end tests that run the exporter and warm-up under a real PyVista interpreter."""

from __future__ import annotations

import json
import sys

import pytest
import pyvista as pv

from pyvista_quicklook import config
from pyvista_quicklook import convert
from pyvista_quicklook import daemon
from pyvista_quicklook import environment
from pyvista_quicklook import render
from pyvista_quicklook import warmup


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

    def never(*args, **kwargs):
        pytest.fail('a cached scene must not be built again')

    monkeypatch.setattr(convert.environment, 'run', never)
    assert convert.scene(source, env) == out


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
    request.write_text(json.dumps({'path': str(source), 'mtime': 1, 'size': 2}))
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
    out = convert.scene(source, hidden)
    assert not convert.info_path(out).exists()
    assert convert.describe(source, out, hidden) is None
    assert convert.describe(source, out, env)['type'] == 'PolyData'
    assert convert.info_path(out).is_file()
    assert not list(config.CACHE_DIR.glob('*.tmp*'))


def test_a_description_that_fails_leaves_the_preview_alone(tmp_path, env):
    """A file that cannot be described still has its preview, and nothing is cached for it."""
    source = tmp_path / 'broken.vtp'
    source.write_text('not a mesh')
    preview = config.CACHE_DIR / 'key.png'
    assert convert.describe(source, preview, env) is None
    assert not list(config.CACHE_DIR.glob('*.json'))


def test_warm_runs_the_warmer_and_leaves_a_stamp(env):
    """Warming loads PyVista in the configured interpreter and records when it finished."""
    assert not warmup.warmed_recently()
    assert warmup.warm(env) > 0
    assert warmup.warmed_recently()


@pytest.mark.skipif(sys.platform != 'darwin', reason='rendering needs a display server on Linux')
def test_still_images_come_from_the_pyvista_command(tmp_path, env):
    """The environment can render a still image, which is what non-interactive mode shows."""
    source = tmp_path / 'sphere.vtp'
    pv.Sphere().save(source)
    png = render.preview(source, env)
    assert png.suffix == '.png'
    assert png.stat().st_size > 0
    assert daemon.produce(source, {**env, 'interactive': False}) == png
    info = convert.describe(source, png, env)
    assert info['type'] == 'PolyData'
    assert convert.info_path(png).name == f'{convert.SCENE_VERSION}-{png.stem}.json'


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
    built = convert.scene(source, settings)
    monkeypatch.setattr(environment, 'run', lambda *args, **kwargs: pytest.fail('read again'))
    assert convert.describe(source, built, settings)['type'] == 'PolyData'


def test_a_description_that_writes_nothing_is_none(tmp_path, env, monkeypatch):
    """An exporter that exits without writing a description leaves the panel out."""
    source = tmp_path / 'sphere.vtp'
    pv.Sphere().save(source)
    monkeypatch.setattr(environment, 'run', lambda *args, **kwargs: None)
    assert convert.describe(source, tmp_path / 'cache' / 'x.png', env) is None


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
