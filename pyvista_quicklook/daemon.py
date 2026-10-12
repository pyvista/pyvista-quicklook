"""Render service that answers preview requests from the Quick Look extension."""

from __future__ import annotations

import contextlib
import json
import os
from pathlib import Path
import shutil
import signal
import threading
import time
from typing import Any
import uuid

from . import cache
from . import config as config_mod
from . import convert as convert_mod
from . import environment
from . import render as render_mod
from . import warmup as warmup_mod
from .environment import RenderError
from .plist import APP_BUNDLE_ID
from .plist import EXT_BUNDLE_ID
from .plist import LEGACY_EXT_BUNDLE_ID

LABEL = 'org.pyvista.pvqld'
LEGACY_LABEL = 'io.github.user27182.pvqld'
REQUEST_SUFFIX = '.pvqlreq'
REPLY_SUFFIX = '.pvqlrep'
STALE_SECONDS = 600

UNREADABLE_MESSAGE = """The render service could not read

    {path}

macOS keeps the Desktop, Documents, and Downloads folders private to each program.
Grant the service access in System Settings under Privacy & Security, or move the
file elsewhere."""


def drop_dir() -> Path:
    """Return the directory the extension and the daemon exchange files in."""
    return Path.home() / 'Library' / 'Containers' / EXT_BUNDLE_ID / 'Data' / 'tmp'


def legacy_drop_dir() -> Path:
    """Return the exchange directory of installations before the org.pyvista identifiers."""
    return Path.home() / 'Library' / 'Containers' / LEGACY_EXT_BUNDLE_ID / 'Data' / 'tmp'


def write_json(path: Path, payload: dict[str, Any]) -> None:
    """Write JSON to a path, renaming it into place so readers never see a partial file."""
    scratch = path.with_suffix(f'.{os.getpid()}.tmp')
    scratch.write_text(json.dumps(payload))
    scratch.replace(path)


_reachable_folders: dict[str, tuple[float, bool]] = {}
FOLDER_MEMO_SECONDS = 300


def folder_is_reachable(path: str) -> bool:
    """Return whether files in a folder can be read, remembering the answer for a while."""
    folder = str(Path(path).parent)
    remembered = _reachable_folders.get(folder)
    now = time.monotonic()
    if remembered and now - remembered[0] < FOLDER_MEMO_SECONDS:
        return remembered[1]
    answer = readable(path)
    _reachable_folders[folder] = (now, answer)
    return answer


def readable(path: str, seconds: float = 2.0) -> bool:
    """Return whether a file can be opened before the guard expires.

    Reading a file in a folder macOS protects can block rather than fail, so the
    attempt is abandoned once the timer fires. A folder counts as readable when it
    can be listed, since a DICOM series is previewed as a whole.
    """

    def expire(signum: int, frame: object) -> None:
        raise TimeoutError

    # SIGALRM is delivered to the main thread, which is where serve() runs this.
    previous = signal.signal(signal.SIGALRM, expire)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        # A DICOM series is a folder, and opening one raises rather than reading.
        if os.path.isdir(path):
            os.listdir(path)
        else:
            with open(path, 'rb') as handle:
                handle.read(1)
    except OSError, TimeoutError:
        return False
    else:
        return True
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def produce(
    target: str | os.PathLike[str],
    config: dict[str, Any],
    identity: tuple[str, int, int] | None = None,
) -> Path:
    """Return a cached preview of a file: an interactive scene, or a still image if asked."""
    if config.get('interactive', True):
        return convert_mod.scene(target, config, identity=identity)
    return render_mod.preview(target, config, identity=identity)


# Descriptions being written on threads of their own, so each file is read once at a time.
_describing: set[Path] = set()
_describing_guard = threading.Lock()


def describe_later(target: str, preview: Path, config: dict[str, Any]) -> threading.Thread | None:
    """Describe a file on a thread of its own, unless that is done, underway, or not wanted."""
    described = convert_mod.info_path(preview)
    with _describing_guard:
        wanted = convert_mod.wants_info(config) and not described.is_file()
        if not wanted or described in _describing:
            return None
        _describing.add(described)

    def work() -> None:
        try:
            convert_mod.describe(target, preview, config)
        finally:
            with _describing_guard:
                _describing.discard(described)

    thread = threading.Thread(target=work, daemon=True)
    thread.start()
    return thread


def handle(request: Path) -> None:
    """Render the file named by one request and write the reply beside it."""
    reply = request.with_suffix(REPLY_SUFFIX)
    try:
        payload = json.loads(request.read_text())
        source = payload['path']
    except (OSError, ValueError, KeyError) as error:
        request.unlink(missing_ok=True)
        write_json(reply, {'ok': False, 'error': f'Malformed preview request: {error}'})
        return

    request.unlink(missing_ok=True)
    identity = None
    if payload.get('mtime') is not None and payload.get('size') is not None:
        identity = (source, int(payload['mtime']), int(payload['size']))

    config = config_mod.load()
    if identity is not None:
        try:
            environment.check_size(identity, config)
        except RenderError as error:
            # The reader can raise the limit, so this is worth saying in full.
            write_json(reply, {'ok': False, 'error': str(error), 'actionable': True})
            return

    # Prefer the original so datasets that reference neighbouring files still resolve.
    target = source if folder_is_reachable(source) else payload.get('copy')
    if not target:
        message = UNREADABLE_MESSAGE.format(path=source)
        write_json(reply, {'ok': False, 'error': message, 'actionable': True})
        return

    try:
        built = produce(target, config, identity)
    except RenderError as error:
        write_json(reply, {'ok': False, 'error': str(error)})
        return
    except Exception as error:  # noqa: BLE001
        write_json(reply, {'ok': False, 'error': f'{type(error).__name__}: {error}'})
        return
    delivered = reply.with_suffix(built.suffix)
    shutil.copyfile(built, delivered)
    kind = 'scene' if built.suffix == '.ply' else 'png'
    answer: dict[str, Any] = {'ok': True, kind: str(delivered)}
    # A scene is described as it is built; a still image is described in a read of its own.
    info = convert_mod.describe(target, built, config, read=False)
    if info is None and target != source:
        # The copy is removed once the reply is read, so it is described before replying.
        info = convert_mod.describe(target, built, config)
    elif info is None:
        # The image is sent at once, and its description is ready the next time.
        describe_later(target, built, config)
    if info is not None:
        answer['info'] = info
    write_json(reply, answer)


def sweep(directory: Path) -> None:
    """Delete replies that no one collected."""
    cutoff = time.time() - STALE_SECONDS
    stale = [
        *directory.glob(f'*{REPLY_SUFFIX}'),
        *directory.glob('*.png'),
        *directory.glob('*.ply'),
    ]
    for entry in stale:
        try:
            if entry.stat().st_mtime < cutoff:
                entry.unlink(missing_ok=True)
        except OSError:
            pass


def warm_in_background(config: dict[str, Any]) -> threading.Thread:
    """Load PyVista in a thread, so requests are still answered while it happens."""

    def run() -> None:
        # The installer may have just warmed everything; do not compete with it.
        if warmup_mod.warmed_recently():
            return
        with contextlib.suppress(Exception):
            warmup_mod.warm(config)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread


def serve(poll: float = 0.05) -> int:
    """Answer preview requests until the process is stopped."""
    directory = drop_dir()
    directory.mkdir(parents=True, exist_ok=True)
    cache.discard_other_versions(convert_mod.SCENE_VERSION)
    startup = config_mod.load()
    if startup.get('warm_on_start', True):
        warm_in_background(startup)
    last_sweep = time.monotonic()
    while True:
        for request in sorted(directory.glob(f'*{REQUEST_SUFFIX}')):
            handle(request)
        if time.monotonic() - last_sweep > 60:
            sweep(directory)
            last_sweep = time.monotonic()
        time.sleep(poll)


def request_preview(source: str, timeout: float = 90) -> Path:
    """Ask the daemon for a preview, for testing the service from the command line."""
    directory = drop_dir()
    directory.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    reply = directory / f'{token}{REPLY_SUFFIX}'
    resolved = Path(source).resolve()
    stat = resolved.stat()
    write_json(
        directory / f'{token}{REQUEST_SUFFIX}',
        {
            'path': str(resolved),
            'mtime': int(stat.st_mtime),
            'size': stat.st_size,
        },
    )

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if reply.is_file():
            payload = json.loads(reply.read_text())
            reply.unlink(missing_ok=True)
            if payload.get('ok'):
                return Path(payload.get('scene') or payload['png'])
            raise RenderError(payload.get('error', 'unknown error'))
        time.sleep(0.05)
    message = (
        f'The render service did not answer within {timeout:.0f} s.\n'
        f'Check that it is running:  launchctl print gui/$UID/{LABEL}'
    )
    raise RenderError(message)


def agent_path() -> Path:
    """Return the path of the launch agent property list."""
    return Path.home() / 'Library' / 'LaunchAgents' / f'{LABEL}.plist'


def legacy_agent_path() -> Path:
    """Return the launch agent path of installations before the org.pyvista identifiers."""
    return Path.home() / 'Library' / 'LaunchAgents' / f'{LEGACY_LABEL}.plist'


def agent_plist(helper: str) -> dict[str, Any]:
    """Return the launch agent definition for the render service."""
    logs = Path.home() / 'Library' / 'Logs'
    return {
        'Label': LABEL,
        'ProgramArguments': [helper, 'daemon'],
        # Listed under the app's name in Login Items, rather than the program's.
        'AssociatedBundleIdentifiers': [APP_BUNDLE_ID],
        'RunAtLoad': True,
        'KeepAlive': True,
        # Background would throttle the renderer; previews answer a key press.
        'ProcessType': 'Interactive',
        'StandardOutPath': str(logs / 'pvqld.log'),
        'StandardErrorPath': str(logs / 'pvqld.log'),
    }
