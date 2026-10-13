"""The preview cache: what identifies a file, and how a preview lands in it."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
from pathlib import Path
import threading
import time
from typing import TYPE_CHECKING
from typing import Any

from . import config as config_mod
from .environment import RenderError
from .environment import log

if TYPE_CHECKING:
    from collections.abc import Callable

CACHE_VERSION = '1'
SUFFIXES = ('*.png', '*.ply')
# The info panel's description of a preview, kept beside it but not counted as one.
SIDECARS = ('*.json',)
# Which file each entry was built from, since its name is a digest that cannot say.
RECORD_SUFFIX = '.src'
RECORDS = (f'*{RECORD_SUFFIX}',)
# A record or entry this young may be half of one landing, so pruning leaves it be.
SETTLE_SECONDS = 60


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


def record_path(entry: Path) -> Path:
    """Return where the record of the file an entry was built from is kept."""
    return entry.with_name(f'{entry.name}{RECORD_SUFFIX}')


def remember(entry: Path, identity: tuple[str, int, int]) -> None:
    """Record which file an entry is built from, before the entry itself lands."""
    source, mtime, size = identity
    record = record_path(entry)
    scratch = record.with_name(f'{record.name}.{scratch_tag()}.tmp')
    scratch.write_text(json.dumps({'path': source, 'mtime': mtime, 'size': size}))
    scratch.replace(record)


def fill(
    out: Path,
    config: dict[str, Any],
    label: str,
    build: Callable[[Path], None],
    identity: tuple[str, int, int],
) -> Path:
    """Return ``out``, calling ``build(scratch)`` to make it unless it is already cached.

    The build writes to a scratch path that is renamed into place only when it is
    complete, so a reader never sees a partial preview. ``identity`` is recorded
    beside it, so pruning can tell when the file it shows has changed or gone.
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
    remember(out, identity)
    scratch.replace(out)
    log(config, f'done {label} in {time.monotonic() - started:.1f}s')
    return out


def entries() -> list[Path]:
    """Return every cached preview, both rendered images and interactive scenes."""
    return files(SUFFIXES)


def files(patterns: tuple[str, ...] = SUFFIXES + SIDECARS + RECORDS) -> list[Path]:
    """Return the cached files matching the patterns, every kind but scratch by default."""
    if not config_mod.CACHE_DIR.is_dir():
        return []
    return [e for pattern in patterns for e in config_mod.CACHE_DIR.glob(pattern)]


def discard_other_versions(version: str) -> int:
    """Delete scenes and descriptions from other exporter versions, returning how many."""
    removed = 0
    for entry in files(('*.ply', *SIDECARS)):
        if not entry.name.startswith(f'{version}-'):
            entry.unlink(missing_ok=True)
            record_path(entry).unlink(missing_ok=True)
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


def unmounted(source: Path) -> bool:
    """Return whether a path is on a volume that is not mounted, such as an unplugged disk."""
    parts = source.parts
    return len(parts) > 2 and parts[1] == 'Volumes' and not Path(*parts[:3]).is_dir()


def outdated(record: Path) -> bool:
    """Return whether the file a record names has gone or changed since its entry was built.

    A file that cannot be looked at, on an unplugged disk or in a folder the service may
    not read, is given the benefit of the doubt: its entry is kept.
    """
    try:
        recorded = json.loads(record.read_text())
        source = Path(recorded['path'])
        expected = (int(recorded['mtime']), int(recorded['size']))
    except OSError, ValueError, KeyError, TypeError:
        return True
    if unmounted(source):
        return False
    try:
        current = identity_of(source)
    except FileNotFoundError, NotADirectoryError:
        return True
    except OSError:
        return False
    return current[1:] != expected


def settled(path: Path, now: float) -> bool:
    """Return whether a file is old enough that nothing is still putting it in place."""
    try:
        return now - path.stat().st_mtime > SETTLE_SECONDS
    except OSError:
        return False


def prune(config: dict[str, Any] | None = None) -> int:
    """Delete entries whose file has gone or changed, returning how many went.

    Entries built before records were kept cannot be checked, so they go too, as do
    records left without an entry.
    """
    now = time.time()
    removed = 0
    for entry in files(SUFFIXES + SIDECARS):
        record = record_path(entry)
        stale = outdated(record) if record.is_file() else settled(entry, now)
        if stale:
            entry.unlink(missing_ok=True)
            record.unlink(missing_ok=True)
            removed += 1
    for record in files(RECORDS):
        entry = record.with_name(record.name.removesuffix(RECORD_SUFFIX))
        if not entry.exists() and settled(record, now):
            record.unlink(missing_ok=True)
    if removed and config is not None:
        log(config, f'pruned {removed} cached entries')
    return removed


def clear() -> int:
    """Delete every cached preview and its description, returning how many previews went."""
    removed = len(entries())
    for entry in files():
        entry.unlink(missing_ok=True)
    return removed
