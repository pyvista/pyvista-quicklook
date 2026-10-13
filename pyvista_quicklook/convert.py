"""Turn mesh files into cached scenes the Quick Look extension can show interactively."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import TYPE_CHECKING
from typing import Any

from . import cache
from . import config as config_mod
from . import environment

if TYPE_CHECKING:
    import os

EXPORTER = Path(__file__).with_name('_scene_export.py')
DESCRIBER = Path(__file__).with_name('_dataset_info.py')
# Reading a header takes well under a second, so a slow one is not worth waiting for.
HEADER_SECONDS = 20


def converter_version(*sources: Path) -> str:
    """Return a short digest of the scripts that make a cached file, which changes with them."""
    return hashlib.sha256(b''.join(source.read_bytes() for source in sources)).hexdigest()[:12]


# Scenes and descriptions carry this in their names, so an update never serves an old one.
SCENE_VERSION = converter_version(EXPORTER, DESCRIBER)


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
        config_mod.theme_token(config),
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
        scratch = described.with_name(f'{described.stem}.{cache.scratch_tag()}.tmp.json')
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


# Formats whose header describes the dataset, as ``_dataset_info.HEADERS`` reads them.
HEADER_SUFFIXES = frozenset({'.pv', '.vtu', '.vtp', '.vti', '.vtr', '.vts'})


def header(source: str | os.PathLike[str], config: dict[str, Any]) -> dict[str, Any] | None:
    """Return what a file's header alone says for the info panel, reading none of its values."""
    if not wants_info(config) or Path(source).suffix.lower() not in HEADER_SUFFIXES:
        return None
    scratch = config_mod.CACHE_DIR / f'header.{cache.scratch_tag()}.tmp.json'
    try:
        scratch.parent.mkdir(parents=True, exist_ok=True)
        command = [
            environment.interpreter(config),
            str(EXPORTER),
            str(source),
            '--header',
            str(scratch),
        ]
        environment.run(
            config,
            command,
            task='Reading the header',
            cwd=Path(source).parent,
            timeout=HEADER_SECONDS,
        )
        info = json.loads(scratch.read_text())
    except environment.RenderError, OSError, ValueError:
        return None
    finally:
        scratch.unlink(missing_ok=True)
    # A header that names no dataset type has nothing for the panel to show.
    return info if isinstance(info, dict) and info.get('type') else None


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
    pending = info_path(identity).with_name(f'{out.stem}.{cache.scratch_tag()}.tmp.json')

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
        if config.get('theme'):
            command += ['--theme', str(config['theme'])]
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
