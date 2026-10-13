"""Render service that answers preview requests from the Quick Look extension."""

from __future__ import annotations

import contextlib
import errno
import functools
import json
import math
import os
from pathlib import Path
import queue
import shutil
import sys
import threading
import time
from typing import TYPE_CHECKING
from typing import Any
import uuid

from . import cache
from . import config as config_mod
from . import convert as convert_mod
from . import environment
from . import preload as preload_mod
from . import render as render_mod
from .environment import RenderError
from .plist import LEGACY_EXT_BUNDLE_ID
from .plist import SERVICE_EXECUTABLE

if TYPE_CHECKING:
    from collections.abc import Iterator

# A new label for the agent the app carries: macOS keys background items by label, and the
# record an unbundled agent left under the old one keeps the new agent from registering.
LABEL = 'org.pyvista.PyVistaQuickLook.Service'
UNBUNDLED_LABEL = 'org.pyvista.pvqld'
LEGACY_LABEL = 'io.github.user27182.pvqld'
REQUEST_SUFFIX = '.pvqlreq'
REPLY_SUFFIX = '.pvqlrep'
# A request the service has picked up, so the next poll does not pick it up again.
TAKEN_SUFFIX = '.pvqltaken'
# What the header says, sent while a large file is still being converted.
EARLY_SUFFIX = '.pvqlpre'
EARLY_BYTES = 32 * 1024 * 1024
STALE_SECONDS = 600
# How much of the extension's wait for a reply is kept in hand.
REPLY_MARGIN_SECONDS = 5
# The longest the pruner waits before rereading its interval.
PRUNE_POLL_SECONDS = 3600
# Folders macOS asks the reader about before the service may look in them: iCloud Drive,
# cloud storage providers, and other apps' data among them.
PRIVATE_FOLDERS = (
    'Desktop',
    'Documents',
    'Downloads',
    'Library/Mobile Documents',
    'Library/CloudStorage',
    'Library/Containers',
)

UNREADABLE_MESSAGE = """The render service could not read

    {path}

macOS keeps the Desktop, Documents, and Downloads folders private to each program.
Grant the service access in System Settings under Privacy & Security, or move the
file elsewhere."""


class NeedsCopyError(Exception):
    """Raised when a file the service cannot read must be copied by the extension first."""


def drop_dir() -> Path:
    """Return the directory the extension and the daemon exchange files in."""
    return config_mod.MIRROR_PATH.parent


def legacy_drop_dir() -> Path:
    """Return the exchange directory of installations before the org.pyvista identifiers."""
    return Path.home() / 'Library' / 'Containers' / LEGACY_EXT_BUNDLE_ID / 'Data' / 'tmp'


def reply_seconds(config: dict[str, Any]) -> float:
    """Return how long the extension waits for a reply: the conversion's limit, and some."""
    return float(config.get('timeout') or config_mod.DEFAULTS['timeout']) + 30


def write_json(path: Path, payload: dict[str, Any]) -> None:
    """Write JSON to a path, renaming it into place so readers never see a partial file."""
    scratch = cache.scratch_path(path)
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
    if answer is None:
        # The file's own trouble, a missing or unreadable one, says nothing about its folder.
        return True
    _reachable_folders[folder] = (now, answer)
    return answer


def readable(path: str, seconds: float = 2.0) -> bool | None:
    """Return whether a file's folder lets it be read, or None when the file itself is the trouble.

    Reading a file in a folder macOS protects can block rather than fail, so the
    attempt is abandoned once the guard expires, leaving the blocked read to a thread
    of its own. A folder counts as readable when it can be listed, since a DICOM
    series is previewed as a whole.
    """
    answer: list[bool | None] = []

    def attempt() -> None:
        try:
            # A DICOM series is a folder, and opening one raises rather than reading.
            if os.path.isdir(path):
                os.listdir(path)
            else:
                with open(path, 'rb') as handle:
                    handle.read(1)
        except OSError as error:
            # TCC refuses a protected folder with EPERM; anything else is the file's own.
            answer.append(False if error.errno == errno.EPERM else None)
        else:
            answer.append(True)

    probe = threading.Thread(target=attempt, daemon=True)
    probe.start()
    probe.join(seconds)
    return answer[0] if answer else False


def cached_preview(identity: tuple[str, int, int] | None, config: dict[str, Any]) -> Path | None:
    """Return the cached preview a request asks for, or None when it must be built."""
    if identity is None or not config.get('cache', True):
        return None
    if config.get('interactive', config_mod.DEFAULTS['interactive']):
        preview = convert_mod.scene_path(identity, config)
    else:
        preview = render_mod.preview_path(identity, config)
    if not cache.holds(preview):
        return None
    environment.log(config, f'hit  {identity[0]}')
    return preview


_headers: dict[tuple[str, int, int], dict[str, Any] | None] = {}
_headers_guard = threading.Lock()


def header_once(
    target: str, config: dict[str, Any], identity: tuple[str, int, int]
) -> dict[str, Any] | None:
    """Return what a file's header says, read once for the requests that follow."""
    with _headers_guard:
        if identity in _headers:
            return _headers[identity]
    info = convert_mod.header(target, config)
    with _headers_guard:
        if len(_headers) >= 32:
            _headers.clear()
        _headers[identity] = info
    return info


def send_early(
    request: Path,
    target: str,
    config: dict[str, Any],
    identity: tuple[str, int, int],
    done: threading.Event,
    lock: threading.Lock,
) -> None:
    """Send what the header says, unless the preview itself has already been sent."""
    info = header_once(target, config, identity)
    with lock:
        if info and not done.is_set():
            write_json(request.with_suffix(EARLY_SUFFIX), {'ok': True, 'info': info})


# A conversion's peak memory, estimated as the interpreter plus a multiple of the file size.
BASE_MEMORY = 200_000_000
MEMORY_PER_FILE_BYTE = 6


class MemoryBudget:
    """Lets conversions run together while their estimated memory fits, and any one alone.

    Waiting conversions start in the order they asked, so a large one is never held off
    by a stream of small ones; background work waits behind any the reader asked for.
    """

    def __init__(self) -> None:
        """Start with nothing running or waiting."""
        self.condition = threading.Condition()
        self.used = 0
        self.running = 0
        self.waiting: list[tuple[bool, int]] = []
        self.tickets = 0

    @contextlib.contextmanager
    def reserve(self, amount: int, limit: int, *, background: bool = False) -> Iterator[None]:
        """Wait until ``amount`` fits under ``limit`` beside what runs, or nothing runs."""
        with self.condition:
            self.tickets += 1
            ticket = (background, self.tickets)
            self.waiting.append(ticket)
            try:
                self.condition.wait_for(
                    lambda: (
                        min(self.waiting) == ticket
                        and (self.running == 0 or self.used + amount <= limit)
                    )
                )
            finally:
                self.waiting.remove(ticket)
            self.used += amount
            self.running += 1
            self.condition.notify_all()
        try:
            yield
        finally:
            with self.condition:
                self.used -= amount
                self.running -= 1
                self.condition.notify_all()


CONVERTING = MemoryBudget()

# Files being previewed, each with a lock and how many requests hold or await it.
_previewing: dict[tuple[Any, ...], list[Any]] = {}
_previewing_guard = threading.Lock()


@contextlib.contextmanager
def one_at_a_time(identity: tuple[str, int, int] | None, config: dict[str, Any]) -> Iterator[None]:
    """Hold back a request for a preview that another request is already building."""
    if identity is None:
        yield
        return
    key = (*identity, config.get('interactive', config_mod.DEFAULTS['interactive']))
    with _previewing_guard:
        entry = _previewing.setdefault(key, [threading.Lock(), 0])
        entry[1] += 1
    try:
        with entry[0]:
            yield
    finally:
        with _previewing_guard:
            entry[1] -= 1
            if not entry[1]:
                del _previewing[key]


def physical_memory() -> int:
    """Return the Mac's memory in bytes."""
    return os.sysconf('SC_PAGE_SIZE') * os.sysconf('SC_PHYS_PAGES')


def memory_budget(config: dict[str, Any]) -> int:
    """Return the memory conversions may use together, in bytes."""
    limit = config.get('max_conversion_memory_mb')
    if limit is None:
        return physical_memory() // 2
    return limit * 1_000_000 if limit else sys.maxsize


def conversion_memory(target: str, identity: tuple[str, int, int] | None) -> int:
    """Return the memory a file's conversion is expected to need at its peak."""
    # A folder's own size says nothing of the series inside, so it is converted alone.
    if identity is None or os.path.isdir(target):
        return sys.maxsize
    return BASE_MEMORY + MEMORY_PER_FILE_BYTE * identity[2]


def produce(
    target: str | os.PathLike[str],
    config: dict[str, Any],
    identity: tuple[str, int, int] | None = None,
) -> Path:
    """Return a cached preview of a file: an interactive scene, or a still image if asked."""
    if config.get('interactive', config_mod.DEFAULTS['interactive']):
        return convert_mod.scene(target, config, identity=identity)
    return render_mod.preview(target, config, identity=identity)


def answer(request: Path) -> None:
    """Handle one request on its own thread, replying with any failure rather than dying."""
    reply = request.with_suffix(REPLY_SUFFIX)
    try:
        handle(request)
    except BaseException as error:  # noqa: BLE001
        if not reply.exists():
            write_json(reply, {'ok': False, 'error': f'{type(error).__name__}: {error}'})
        request.unlink(missing_ok=True)


# Still images waiting to be described after their replies, and the files among them.
_descriptions: queue.Queue[tuple[str, dict[str, Any], tuple[str, int, int]]] = queue.Queue()
_describing: set[Path] = set()
_describing_guard = threading.Lock()


def describe_queued() -> None:
    """Describe queued files one after another, so their reads never pile up."""
    while True:
        target, config, identity = _descriptions.get()
        try:
            # Reading a file again takes as much memory as building its preview did, and
            # waits behind any preview the reader asked for.
            amount = conversion_memory(target, identity)
            with CONVERTING.reserve(amount, memory_budget(config), background=True):
                convert_mod.describe(target, config, identity)
        except Exception as error:  # noqa: BLE001
            environment.log(config, f'describing {target} failed: {error}')
        finally:
            with _describing_guard:
                _describing.discard(convert_mod.info_path(identity))
            _descriptions.task_done()


@functools.cache
def describer() -> threading.Thread:
    """Return the thread that describes queued files, starting it the first time."""
    thread = threading.Thread(target=describe_queued, daemon=True)
    thread.start()
    return thread


def describe_later(
    target: str, config: dict[str, Any], identity: tuple[str, int, int] | None
) -> bool:
    """Queue a file to be described, unless that is done, queued, or not wanted."""
    if identity is None or not convert_mod.wants_info(config):
        return False
    described = convert_mod.info_path(identity)
    with _describing_guard:
        if described.is_file() or described in _describing:
            return False
        _describing.add(described)
    describer()
    _descriptions.put((target, config, identity))
    return True


# Folders of copies the extension staged that a request still reads, kept from the sweep.
_staged: set[Path] = set()
_staged_guard = threading.Lock()


@contextlib.contextmanager
def staged_in_use(copy: str | None) -> Iterator[None]:
    """Keep the folder of a staged copy from the sweep while its request is handled."""
    folder = Path(copy).parent.resolve() if copy else None
    if folder is not None:
        with _staged_guard:
            _staged.add(folder)
    try:
        yield
    finally:
        if folder is not None:
            with _staged_guard:
                _staged.discard(folder)


def handle(request: Path) -> None:
    """Render the file named by one request and write the reply beside it."""
    reply = request.with_suffix(REPLY_SUFFIX)
    try:
        asked = request.stat().st_mtime
        payload = json.loads(request.read_text())
        source = payload.get('path') if isinstance(payload, dict) else None
        if not isinstance(source, str):
            message = 'the request must be an object naming a path'
            raise TypeError(message)
    except (OSError, ValueError, KeyError, TypeError) as error:
        request.unlink(missing_ok=True)
        write_json(reply, {'ok': False, 'error': f'Malformed preview request: {error}'})
        return
    request.unlink(missing_ok=True)
    with staged_in_use(payload.get('copy')):
        respond(request, source, payload, asked)


def description_only(
    source: str, config: dict[str, Any], identity: tuple[str, int, int] | None
) -> dict[str, Any] | None:
    """Return a file's cached description, or what its header says, converting nothing."""
    described = convert_mod.describe(source, config, identity, read=False)
    if described is not None or not folder_is_reachable(source):
        return described
    return convert_mod.header(source, config)


def respond(request: Path, source: str, payload: dict[str, Any], asked: float) -> None:
    """Answer a request for ``source``, given what it asked and when."""
    reply = request.with_suffix(REPLY_SUFFIX)
    identity = None
    if payload.get('mtime') is not None and payload.get('size') is not None:
        identity = (source, int(float(payload['mtime'])), int(float(payload['size'])))

    config = config_mod.load()
    if payload.get('describe') is True:
        write_json(reply, {'ok': True, 'info': description_only(source, config, identity)})
        return
    if 'scene' in payload:
        # A file with a picture of its own asks for the 3D view by its tab, whatever the settings.
        config = {**config, 'interactive': payload['scene'] is True}
    if config_mod.saved_theme(config):
        # `pyvista plot` takes a theme by name only, so a saved theme is drawn in 3D.
        config = {**config, 'interactive': True}
    ready = cached_preview(identity, config)
    if payload.get('force') is True or ready is not None:
        # Asked for whatever its size, or previewed already, so no step of it applies the limit.
        config = {**config, 'max_file_size_mb': 0}
    if identity is not None:
        try:
            environment.check_size(identity, config)
        except RenderError as error:
            # The reader can preview it anyway or raise the limit, so this is worth saying in full.
            refusal = {'ok': False, 'error': str(error), 'too_large': True}
            # Nothing is copied above the limit, so only a file the service can reach is described.
            if folder_is_reachable(source) and (info := convert_mod.header(source, config)):
                refusal['info'] = info
            write_json(reply, refusal)
            return

    reachable = folder_is_reachable(source)
    # Prefer the original so datasets that reference neighbouring files still resolve; a
    # cached preview is served without reading the file at all.
    target = source if reachable or ready is not None else payload.get('copy')
    if not target:
        message = UNREADABLE_MESSAGE.format(path=source)
        # The extension copies the file somewhere readable and asks again, when it can.
        write_json(reply, {'ok': False, 'error': message, 'needs_copy': True})
        return

    done, lock = threading.Event(), threading.Lock()
    large = identity is not None and identity[2] >= EARLY_BYTES
    if large and convert_mod.wants_info(config) and ready is None:
        early = threading.Thread(
            target=send_early, args=(request, target, config, identity, done, lock)
        )
        early.start()

    def answer_with(message: dict[str, Any]) -> None:
        """Send the final reply, after which no early one may follow."""
        with lock:
            done.set()
            write_json(reply, message)

    def find_or_build() -> tuple[Path, dict[str, Any] | None]:
        """Return the preview and its description, building the preview if it is not cached."""
        nonlocal target
        # Quick Look asks for each file twice; the second request reuses the first's preview.
        with one_at_a_time(identity, config):
            ready = cached_preview(identity, config)
            if ready is None and target == source and not reachable:
                # Found cached at first and pruned since, so the file must be read after all.
                target = payload.get('copy') or ''
                if not target:
                    raise NeedsCopyError
            if ready is not None:
                # A cached preview is only copied: no memory, and no look at the file.
                return ready, convert_mod.describe(target, config, identity, read=False)
            needed = conversion_memory(target, identity)
            with CONVERTING.reserve(needed, memory_budget(config)):
                built = produce(target, config, identity)
                # A scene is described as it is built; a still image in a read of its own.
                info = convert_mod.describe(target, config, identity, read=False)
                if info is None and target != source:
                    # The copy is removed once the reply is read, so it is described now, in
                    # the time the extension still waits; past that the reply goes without.
                    left = reply_seconds(config) - REPLY_MARGIN_SECONDS - (time.time() - asked)
                    if left > 1:
                        info = convert_mod.describe(target, config, identity, timeout=left)
        return built, info

    try:
        built, info = find_or_build()
        delivered = reply.with_suffix(built.suffix)
        try:
            shutil.copyfile(built, delivered)
        except FileNotFoundError:
            # Pruned between being found and being copied, so it is found or built again.
            built, info = find_or_build()
            delivered = reply.with_suffix(built.suffix)
            shutil.copyfile(built, delivered)
    except NeedsCopyError:
        message = UNREADABLE_MESSAGE.format(path=source)
        answer_with({'ok': False, 'error': message, 'needs_copy': True})
        return
    except environment.NotAMeshError as error:
        # Only then does the extension show the file as text or as its icon instead.
        answer_with({'ok': False, 'error': str(error), 'not_a_mesh': True})
        return
    except RenderError as error:
        answer_with({'ok': False, 'error': str(error)})
        return
    except Exception as error:  # noqa: BLE001
        answer_with({'ok': False, 'error': f'{type(error).__name__}: {error}'})
        return
    kind = 'scene' if built.suffix == '.ply' else 'png'
    preview: dict[str, Any] = {'ok': True, kind: str(delivered)}
    if info is not None:
        preview['info'] = info
    answer_with(preview)
    if info is None and target == source and reachable:
        # The image went out first, and its description is ready the next time.
        describe_later(target, config, identity)


def sweep(directory: Path) -> None:
    """Delete replies that no one collected."""
    cutoff = time.time() - STALE_SECONDS
    stale = [
        *directory.glob(f'*{REPLY_SUFFIX}'),
        *directory.glob(f'*{EARLY_SUFFIX}'),
        *directory.glob(f'*{TAKEN_SUFFIX}'),
        *directory.glob('*.png'),
        *directory.glob('*.ply'),
        # Copies the extension staged and left, when Quick Look stopped it or it moved on.
        *directory.glob('*.staging'),
    ]
    with _staged_guard:
        in_use = set(_staged)
    for entry in stale:
        try:
            if entry.stat().st_mtime >= cutoff or entry.resolve() in in_use:
                continue
            if entry.is_dir():
                shutil.rmtree(entry, ignore_errors=True)
            else:
                entry.unlink(missing_ok=True)
        except OSError:
            pass


def preload_in_background(config: dict[str, Any]) -> threading.Thread:
    """Load PyVista in a thread, so requests are still answered while it happens."""

    def run() -> None:
        # The installer may have just preloaded everything; do not compete with it.
        if preload_mod.preloaded_recently():
            return
        try:
            preload_mod.run(config)
        except Exception as error:  # noqa: BLE001
            # Requests are still answered; the next one says what is wrong with the setup.
            environment.log(config, f'preload failed: {error}')

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread


def check_minutes(config: dict[str, Any]) -> float:
    """Return the minutes between checks of the cache, where 0 means never.

    A value written by hand that is not a number falls back to the default, and a check
    is never more often than once a minute.
    """
    default = config_mod.DEFAULTS['cache_check_minutes']
    value = config.get('cache_check_minutes')
    try:
        minutes = default if value is None or isinstance(value, bool) else float(value)
    except TypeError, ValueError:
        minutes = default
    if not math.isfinite(minutes):
        minutes = default
    return 0 if minutes <= 0 else max(minutes, 1)


def prune_due(minutes: float, last: float | None, now: float) -> bool:
    """Return whether the cache is due a check for files that have changed or gone."""
    return minutes > 0 and (last is None or now - last >= minutes * 60)


def private_root(source: Path) -> Path | None:
    """Return the private folder or volume holding a file, which macOS grants as a whole."""
    for folder in (Path.home() / name for name in PRIVATE_FOLDERS):
        if source.is_relative_to(folder):
            return folder
    parts = source.parts
    return Path(*parts[:3]) if len(parts) > 2 and parts[1] == 'Volumes' else None


def may_look(source: Path) -> bool:
    """Return whether the pruner may look at a file without macOS asking the reader first.

    A file in a private folder or on another volume is looked at only once a preview has
    found a folder in it readable.
    """
    root = private_root(source)
    if root is None:
        return True
    # A copy, since request threads add to it while this runs.
    remembered = dict(_reachable_folders)
    return any(
        readable and Path(folder).is_relative_to(root)
        for folder, (_, readable) in remembered.items()
    )


def prune_periodically() -> None:
    """Drop cached previews of changed or deleted files, as often as the config asks."""
    last = None
    while True:
        config = config_mod.load()
        minutes = check_minutes(config)
        # The wall clock, so a Mac that sleeps overnight still checks once a day.
        if prune_due(minutes, last, time.time()):
            # A file that cannot be looked at blocks only this thread, never a preview.
            try:
                cache.prune(config, may_look)
            except Exception as error:  # noqa: BLE001
                environment.log(config, f'prune failed: {type(error).__name__}: {error}')
            last = time.time()
        time.sleep(min(PRUNE_POLL_SECONDS, minutes * 60) if minutes else PRUNE_POLL_SECONDS)


def prune_in_background() -> threading.Thread:
    """Start the thread that keeps the cache to files that still exist as they were."""
    thread = threading.Thread(target=prune_periodically, daemon=True)
    thread.start()
    return thread


def serve(poll: float = 0.05) -> int:
    """Answer preview requests until the process is stopped."""
    directory = drop_dir()
    directory.mkdir(parents=True, exist_ok=True)
    cache.discard_other_versions(convert_mod.SCENE_VERSION)
    cache.discard_scratch(STALE_SECONDS)
    startup = config_mod.load()
    if startup.get('preload_on_start', True):
        preload_in_background(startup)
    prune_in_background()
    last_sweep = last_mirror = time.monotonic()
    while True:
        # Settings edited by hand reach the extension within a second.
        if time.monotonic() - last_mirror >= 1:
            config_mod.mirror()
            last_mirror = time.monotonic()
        for request in sorted(directory.glob(f'*{REQUEST_SUFFIX}')):
            taken = request.with_suffix(TAKEN_SUFFIX)
            try:
                request.rename(taken)
            except OSError:
                continue
            # Each request is answered on its own thread, so its header is sent at once even
            # while another file's preview is being built.
            threading.Thread(target=answer, args=(taken,), daemon=True).start()
        if time.monotonic() - last_sweep > 60:
            sweep(directory)
            last_sweep = time.monotonic()
        time.sleep(poll)


def request_preview(source: str, timeout: float = 90, scene: bool | None = None) -> Path:
    """Ask the daemon for a preview, the picture or the 3D view when ``scene`` says which."""
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
            **({} if scene is None else {'scene': scene}),
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
    """Return where installs before the bundled agent wrote the launch agent."""
    return Path.home() / 'Library' / 'LaunchAgents' / f'{UNBUNDLED_LABEL}.plist'


def legacy_agent_path() -> Path:
    """Return the launch agent path of installations before the org.pyvista identifiers."""
    return Path.home() / 'Library' / 'LaunchAgents' / f'{LEGACY_LABEL}.plist'


def bundled_agent_plist() -> dict[str, Any]:
    """Return the agent the holder app carries, which macOS lists under the app."""
    return {
        'Label': LABEL,
        # Finds pvql and opens the log, neither of which a bundled plist can name.
        'BundleProgram': f'Contents/MacOS/{SERVICE_EXECUTABLE}',
        'RunAtLoad': True,
        'KeepAlive': True,
        # Background would throttle the renderer; previews answer a key press.
        'ProcessType': 'Interactive',
    }
