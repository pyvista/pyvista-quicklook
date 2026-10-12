"""The preview cache: what identifies a file, and how a preview lands in it."""

from __future__ import annotations

import contextlib
import hashlib
import os
import threading
import time
from typing import TYPE_CHECKING
from typing import Any

from . import config as config_mod
from .environment import RenderError
from .environment import log

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

CACHE_VERSION = '1'
SUFFIXES = ('*.png', '*.ply')
# The info panel's description of a preview, kept beside it but not counted as one.
SIDECARS = ('*.json',)


def identity_of(path: Path) -> tuple[str, int, int]:
    """Return the path, whole-second modification time, and size that identify a file."""
    stat = path.stat()
    return str(path), int(stat.st_mtime), stat.st_size


def digest(identity: tuple[str, int, int], settings: list[str]) -> str:
    """Return a cache key for a file previewed with the given settings."""
    source, mtime, size = identity
    parts = [CACHE_VERSION, source, str(mtime), str(size), *settings]
    return hashlib.sha256('\0'.join(parts).encode()).hexdigest()


def scratch_tag() -> str:
    """Return a tag naming this thread's scratch files, so concurrent builds never share one."""
    return f'{os.getpid()}.{threading.get_ident()}'


def fill(out: Path, config: dict[str, Any], label: str, build: Callable[[Path], None]) -> Path:
    """Return ``out``, calling ``build(scratch)`` to make it unless it is already cached.

    The build writes to a scratch path that is renamed into place only when it is
    complete, so a reader never sees a partial preview.
    """
    if config.get('cache', True) and out.is_file() and out.stat().st_size:
        log(config, f'hit  {label}')
        return out

    out.parent.mkdir(parents=True, exist_ok=True)
    scratch = out.with_name(f'{out.stem}.{scratch_tag()}.tmp{out.suffix}')
    log(config, f'miss {label}')
    started = time.monotonic()
    try:
        build(scratch)
        if not (scratch.is_file() and scratch.stat().st_size):
            message = f'Nothing was written for {label}.'
            raise RenderError(message)
    except BaseException:
        scratch.unlink(missing_ok=True)
        log(config, f'fail {label}')
        raise
    scratch.replace(out)
    log(config, f'done {label} in {time.monotonic() - started:.1f}s')
    return out


def entries() -> list[Path]:
    """Return every cached preview, both rendered images and interactive scenes."""
    return files(SUFFIXES)


def files(patterns: tuple[str, ...] = SUFFIXES + SIDECARS) -> list[Path]:
    """Return the cached files matching the patterns, previews and descriptions by default."""
    if not config_mod.CACHE_DIR.is_dir():
        return []
    return [e for pattern in patterns for e in config_mod.CACHE_DIR.glob(pattern)]


def discard_other_versions(version: str) -> int:
    """Delete scenes and descriptions from other exporter versions, returning how many."""
    removed = 0
    for entry in files(('*.ply', *SIDECARS)):
        if not entry.name.startswith(f'{version}-'):
            entry.unlink(missing_ok=True)
            removed += 1
    return removed


def discard_scratch(older_than: float) -> int:
    """Delete scratch files a stopped build left behind, once older than ``older_than`` seconds."""
    cutoff = time.time() - older_than
    removed = 0
    for entry in files(('*.tmp.*',)):
        with contextlib.suppress(OSError):
            if entry.stat().st_mtime < cutoff:
                entry.unlink()
                removed += 1
    return removed


def clear() -> int:
    """Delete every cached preview and its description, returning how many previews went."""
    removed = len(entries())
    for entry in files():
        entry.unlink(missing_ok=True)
    return removed
