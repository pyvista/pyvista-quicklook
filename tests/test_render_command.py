"""Tests for how still images are keyed, commanded, and refused."""

from __future__ import annotations

import pytest

from pyvista_quicklook import convert
from pyvista_quicklook import environment
from pyvista_quicklook import render


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


def test_cache_keys_follow_the_theme_and_its_file(tmp_path):
    """A different theme, or an edited theme file, draws previews again."""
    identity = ('/mesh.vtu', 1, 2)
    saved = tmp_path / 'theme.json'
    saved.write_text('{}')
    keys = [
        (render.cache_key(identity, config), convert.scene_key(identity, config))
        for config in ({}, {'theme': 'dark'}, {'theme': str(saved)})
    ]
    saved.write_text('{"color": "red"}')
    keys.append(
        (
            render.cache_key(identity, {'theme': str(saved)}),
            convert.scene_key(identity, {'theme': str(saved)}),
        )
    )
    saved.unlink()
    keys.append(
        (
            render.cache_key(identity, {'theme': str(saved)}),
            convert.scene_key(identity, {'theme': str(saved)}),
        )
    )
    assert len({still for still, _ in keys}) == len({scene for _, scene in keys}) == 5


def test_the_theme_reaches_every_drawing_command():
    """``pyvista plot`` and scene export are both given the theme."""
    config = {'window_size': [64, 32], 'theme': 'dark'}
    command = render.build_command('/bin/pyvista', 'in.vtu', 'out.png', config)
    assert command[command.index('--theme') + 1] == 'dark'
    with pytest.raises(environment.RenderError, match='by name only'):
        render.build_command(
            '/bin/pyvista', 'in.vtu', 'out.png', {**config, 'theme': '/a/theme.json'}
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


def test_preview_reports_a_missing_file(tmp_path):
    """A path that does not exist is reported plainly."""
    with pytest.raises(environment.RenderError, match='No such file'):
        render.preview(tmp_path / 'absent.vtu', {})
