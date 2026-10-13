"""Render mesh files to cached PNG stills, on a transparent background or with ``pyvista plot``."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
from typing import Any

from . import cache
from . import config as config_mod
from . import environment

if TYPE_CHECKING:
    import os

# Draws a still as pyvista-zstd draws the picture a .pv file carries.
STILL = Path(__file__).with_name('_still.py')


def cache_key(identity: tuple[str, int, int], config: dict[str, Any]) -> str:
    """Return the cache key for a file under the current render settings."""
    settings = [
        repr(config.get('window_size')),
        repr(config.get('background')),
        repr(config.get('extra_args')),
        'transparent',
    ]
    return cache.digest(identity, settings)


def preview_path(identity: tuple[str, int, int], config: dict[str, Any]) -> Path:
    """Return where a file's still image is cached."""
    return config_mod.CACHE_DIR / f'{cache_key(identity, config)}.png'


def uses_pyvista_plot(config: dict[str, Any]) -> bool:
    """Return whether the settings ask ``pyvista plot`` to draw stills, by passing it arguments."""
    return bool(config.get('background') or config.get('extra_args'))


def still_command(interpreter: str, path: Path, out: Path, config: dict[str, Any]) -> list[str]:
    """Return the command that draws a still on a transparent background."""
    width, height = config.get('window_size') or [1024, 1024]
    return [interpreter, str(STILL), str(path), str(out), str(int(width)), str(int(height))]


def build_command(executable: str, path: Path, out: Path, config: dict[str, Any]) -> list[str]:
    """Return the ``pyvista plot`` command used to render a preview."""
    width, height = config.get('window_size') or [1024, 1024]
    command = [
        executable,
        'plot',
        str(path),
        '--off-screen',
        '--no-interactive',
        '--screenshot',
        str(out),
        '--window-size',
        str(int(width)),
        str(int(height)),
    ]
    if config.get('background'):
        command += ['--background', str(config['background'])]
    command += [str(arg) for arg in config.get('extra_args') or []]
    return command


def preview(
    source: str | os.PathLike[str],
    config: dict[str, Any] | None = None,
    identity: tuple[str, int, int] | None = None,
) -> Path:
    """Return the path to a PNG preview of a mesh file, rendering it if not cached.

    ``identity`` names the file the preview belongs to when ``source`` is a copy of it.
    """
    config = config if config is not None else config_mod.load()
    path = environment.source_path(source)
    identity = identity or cache.identity_of(path)
    environment.check_size(identity, config)

    def build(scratch: Path) -> None:
        if not uses_pyvista_plot(config):
            command = still_command(environment.interpreter(config), path, scratch, config)
            environment.run(config, command, task=f'Rendering {path.name}', cwd=path.parent)
            return
        executable = config_mod.resolve_pyvista(config)
        if executable is None:
            message = 'No pyvista command-line interface beside the configured interpreter.'
            raise environment.RenderError(message)
        command = build_command(executable, path, scratch, config)
        environment.run(config, command, task=f'Rendering {path.name}', cwd=path.parent)

    return cache.fill(preview_path(identity, config), config, str(path), build)
