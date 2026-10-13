"""End-to-end tests that run the exporter and warm-up under a real PyVista interpreter."""

from __future__ import annotations

import json
from pathlib import Path
import sys

import numpy as np
import pytest
import pyvista as pv

from pyvista_quicklook import cache
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
    assert not list(config.CACHE_DIR.glob('*.tmp*'))


def test_only_a_cached_description_is_given_without_reading(tmp_path, env, monkeypatch):
    """Asked not to read, describe returns what is cached and starts nothing."""
    source = tmp_path / 'sphere.vtp'
    pv.Sphere().save(source)
    monkeypatch.setattr(environment, 'run', lambda *args, **kwargs: pytest.fail('read'))
    assert convert.describe(source, env, read=False) is None
    monkeypatch.undo()
    assert convert.describe(source, env)['type'] == 'PolyData'
    assert convert.describe(source, env, read=False)['type'] == 'PolyData'


def test_a_missing_file_or_environment_is_not_remembered(tmp_path, env):
    """Nothing is cached when the file is gone or no environment is set up, as both may change."""
    assert convert.describe(tmp_path / 'gone.vtp', env) is None
    source = tmp_path / 'sphere.vtp'
    pv.Sphere().save(source)
    assert convert.describe(source, {**env, 'python': '/no/such/python'}) is None
    assert not list(config.CACHE_DIR.glob('*.json'))


def test_warm_runs_the_warmer_and_leaves_a_stamp(env):
    """Warming loads PyVista in the configured interpreter and records when it finished."""
    assert not warmup.warmed_recently()
    assert warmup.warm(env) > 0
    assert warmup.warmed_recently()


@pytest.mark.skipif(sys.platform != 'darwin', reason='rendering needs a display server on Linux')
def test_still_images_are_drawn_on_a_transparent_background(tmp_path, env, monkeypatch):
    """The environment renders a still, cropped to the dataset with nothing drawn behind it."""
    source = tmp_path / 'sphere.vtp'
    pv.Sphere().save(source)
    png = render.preview(source, {**env, 'window_size': [128, 128]})
    alpha = pixels(png)[..., 3]
    side = alpha.shape[0]
    assert alpha.shape == (side, side)
    assert alpha[side // 2, side // 2] == 255
    # Cropped to the sphere, which then touches the margin on at least one pair of sides.
    rows, columns = np.nonzero(alpha)
    gaps = [rows.min(), side - 1 - rows.max(), columns.min(), side - 1 - columns.max()]
    assert min(gaps) <= np.ceil(0.05 * side) + 1
    assert alpha[0, 0] == 0
    png = render.preview(source, env)
    assert daemon.produce(source, {**env, 'interactive': False}) == png
    assert convert.describe(source, env)['type'] == 'PolyData'
    # The description belongs to the file, so other render settings do not read it again.
    monkeypatch.setattr(environment, 'run', lambda *args, **kwargs: pytest.fail('read again'))
    assert convert.describe(source, {**env, 'window_size': [64, 64]})['type'] == 'PolyData'


def pixels(png):
    """Return a PNG's pixels as rows, top row first."""
    picture = pv.read(png)
    width, height = picture.dimensions[:2]
    return np.flipud(np.asarray(picture.active_scalars).reshape(height, width, -1))


@pytest.mark.skipif(sys.platform != 'darwin', reason='rendering needs a display server on Linux')
def test_a_background_draws_the_still_with_the_pyvista_command(tmp_path, env):
    """Asking for a background colour draws the still opaque with ``pyvista plot``."""
    source = tmp_path / 'sphere.vtp'
    pv.Sphere().save(source)
    png = render.preview(source, {**env, 'window_size': [64, 64], 'background': 'white'})
    corner = pixels(png)[0, 0]
    assert pixels(png).shape[:2] == (64, 64)
    assert (corner[:3] == 255).all()


@pytest.mark.skipif(sys.platform != 'darwin', reason='rendering needs a display server on Linux')
def test_a_saved_theme_colours_the_scene_and_the_still(tmp_path, env):
    """A theme saved as JSON reaches both the exporter and the still renderer."""
    theme = pv.themes.DocumentTheme()
    theme.color = 'red'
    theme.save(tmp_path / 'theme.json')
    settings = {**env, 'window_size': [64, 64], 'theme': str(tmp_path / 'theme.json')}
    source = tmp_path / 'sphere.vtp'
    pv.Sphere().save(source)
    assert (pv.read(convert.scene(source, settings))['RGB'] == (255, 0, 0)).all()
    drawn = pixels(render.preview(source, settings))
    covered = drawn[drawn[..., 3] == 255]
    assert (covered[:, 0] > 3 * covered[:, 1:3].max(axis=1)).all()


@pytest.mark.parametrize('name', ['dark', 'paraview'])
def test_a_named_theme_reaches_pyvista_plot(tmp_path, env, name):
    """``pyvista plot`` draws with the theme it is given by name."""
    source = tmp_path / 'sphere.vtp'
    pv.Sphere().save(source)
    png = render.preview(
        source, {**env, 'window_size': [32, 32], 'background': 'white', 'theme': name}
    )
    assert pixels(png).shape[:2] == (32, 32)


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
    with pytest.raises(environment.NotAMeshError, match='no surface to show'):
        render.preview(notes, env)
    with pytest.raises(environment.RenderError) as raised:
        environment.run(env, [sys.executable, '-c', 'raise SystemExit(1)'], task='Failing')
    assert not isinstance(raised.value, environment.NotAMeshError)
