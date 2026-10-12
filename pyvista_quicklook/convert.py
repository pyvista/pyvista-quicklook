"""Turn mesh files into cached scenes the Quick Look extension can show interactively."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import threading
from typing import Any

from . import cache
from . import config as config_mod
from . import environment

EXPORTER = Path(__file__).with_name('_scene_export.py')
DESCRIBER = Path(__file__).with_name('_dataset_info.py')


def converter_version(exporter: Path = EXPORTER, describer: Path = DESCRIBER) -> str:
    """Return a short digest of the exporter and describer, which changes with their output."""
    return hashlib.sha256(exporter.read_bytes() + describer.read_bytes()).hexdigest()[:12]


# Scenes and descriptions carry this in their names, so an update never serves an old one.
SCENE_VERSION = converter_version()


def budget(config: dict[str, Any], key: str) -> int:
    """Return a point budget from the configuration, where zero means no limit."""
    configured = config.get(key)
    return int(config_mod.DEFAULTS[key] if configured is None else configured)


def scene_key(identity: tuple[str, int, int], config: dict[str, Any]) -> str:
    """Return the cache key for a file's interactive scene."""
    settings = [
        SCENE_VERSION,
        repr(config.get('max_scene_points')),
        repr(config.get('max_glyph_points')),
    ]
    return cache.digest(identity, settings)


def info_path(identity: tuple[str, int, int]) -> Path:
    """Return where the info panel's description of a file is kept, whatever its preview."""
    return config_mod.CACHE_DIR / f'{SCENE_VERSION}-{cache.digest(identity, ["info"])}.json'


def wants_info(config: dict[str, Any]) -> bool:
    """Return whether the info panel is shown at all."""
    return config.get('info_panel', config_mod.DEFAULTS['info_panel']) != 'hidden'


def describe(
    source: str | os.PathLike[str],
    config: dict[str, Any],
    identity: tuple[str, int, int] | None = None,
    *,
    read: bool = True,
    timeout: float | None = None,
) -> dict[str, Any] | None:
    """Return the info panel's description of a file, describing it if not cached.

    With ``read`` false only a cached description is returned, and the file is not read.
    ``timeout`` limits the read, which otherwise has the conversion's own limit.
    """
    if not wants_info(config):
        return None
    try:
        path = environment.source_path(source) if read or identity is None else Path(source)
        identity = identity or cache.identity_of(path)
    except environment.RenderError, OSError:
        return None
    described = info_path(identity)
    # Named after the file's identity and the describer's version, so never stale.
    if not described.is_file():
        if not read:
            return None
        scratch = described.with_name(
            f'{described.stem}.{os.getpid()}.{threading.get_ident()}.tmp.json'
        )
        try:
            command = [environment.interpreter(config), str(EXPORTER), str(path), '--info']
            described.parent.mkdir(parents=True, exist_ok=True)
            environment.run(
                config,
                [*command, str(scratch)],
                task='Describing',
                cwd=path.parent,
                timeout=timeout,
            )
            # A file the describer read but could not describe is remembered as such.
            if not scratch.is_file():
                scratch.write_text('{}')
            scratch.replace(described)
        except environment.RenderError, OSError:
            # A failure that may pass, such as a timeout, is tried again next time.
            return None
        finally:
            scratch.unlink(missing_ok=True)
    try:
        info = json.loads(described.read_text())
    except OSError, ValueError:
        return None
    return info or None


def scene_path(identity: tuple[str, int, int], config: dict[str, Any]) -> Path:
    """Return where a file's scene is cached."""
    return config_mod.CACHE_DIR / f'{SCENE_VERSION}-{scene_key(identity, config)}.ply'


def scene(
    source: str | os.PathLike[str],
    config: dict[str, Any] | None = None,
    identity: tuple[str, int, int] | None = None,
) -> Path:
    """Return the path to a PLY scene for a mesh file, converting it if not cached."""
    config = config if config is not None else config_mod.load()
    path = environment.source_path(source)
    identity = identity or cache.identity_of(path)
    environment.check_size(identity, config)

    out = scene_path(identity, config)
    # The export describes the file in the same read, kept aside until the scene is in.
    pending = info_path(identity).with_name(f'{out.stem}.{os.getpid()}.tmp.json')

    def build(scratch: Path) -> None:
        command = [
            environment.interpreter(config),
            str(EXPORTER),
            str(path),
            str(scratch),
            '--max-points',
            str(budget(config, 'max_scene_points')),
            '--max-glyphs',
            str(budget(config, 'max_glyph_points')),
        ]
        if wants_info(config):
            command += ['--info', str(pending)]
        environment.run(config, command, task=f'Converting {path.name}', cwd=path.parent)

    try:
        built = cache.fill(out, config, f'{path} (scene)', build)
        if pending.is_file():
            pending.replace(info_path(identity))
    finally:
        pending.unlink(missing_ok=True)
    return built
