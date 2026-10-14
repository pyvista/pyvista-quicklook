"""Command-line interface for the PyVista Quick Look helper."""

from __future__ import annotations

import argparse
import contextlib
import os
from pathlib import Path
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any

from . import __version__
from . import cache as cache_mod
from . import config as config_mod
from . import convert as convert_mod
from . import daemon as daemon_mod
from . import plist as plist_mod
from . import preload as preload_mod
from .environment import RenderError
from .formats import FORMATS
from .formats import NATIVE_NAMES
from .formats import NATIVE_TYPES
from .formats import SYSTEM_TYPES
from .formats import UNCLAIMED
from .formats import resolve_extensions
from .formats import uti_for

# Where the app may have been installed, and where the render service writes.
APP_DIRS = (Path.home() / 'Applications', Path('/Applications'))
SERVICE_LOG = daemon_mod.LOG_PATH
LSREGISTER = (
    '/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework'
    '/Support/lsregister'
)

# Where each release attaches its app and the installer that sets it up.
RELEASES = 'https://github.com/pyvista/pyvista-quicklook/releases'

# A tetrahedron in legacy VTK format, used by ``pvql doctor`` as a smoke test.
SMOKE_MESH = """# vtk DataFile Version 3.0
pvql doctor
ASCII
DATASET POLYDATA
POINTS 4 float
0 0 0
1 0 0
0 1 0
0 0 1
POLYGONS 4 16
3 0 1 2
3 0 1 3
3 0 2 3
3 1 2 3
"""


def claimed_extensions(config: dict) -> list[str]:
    """Return the extensions claimed under the current configuration."""
    extensions = config.get('extensions') or {}
    return resolve_extensions(extensions.get('add'), extensions.get('remove'))


def cmd_preview(args: argparse.Namespace) -> int:
    """Build a preview of a file and print the path of the cached scene or image."""
    config = config_mod.load()
    if args.no_cache:
        config['cache'] = False
    try:
        out = daemon_mod.produce(args.path, config)
    except RenderError as error:
        print(error, file=sys.stderr)
        return 1
    if args.output:
        shutil.copyfile(out, args.output)
        out = Path(args.output)
    print(out)
    return 0


def cmd_daemon(_: argparse.Namespace) -> int:
    """Run the render service in the foreground."""
    return daemon_mod.serve()


def service_target(label: str = daemon_mod.LABEL) -> str:
    """Return the launchd name of a service in this user's session."""
    return f'gui/{os.getuid()}/{label}'


def unload_service(label: str = daemon_mod.LABEL) -> None:
    """Stop and unload a service, whether or not it is loaded."""
    subprocess.run(
        ['/bin/launchctl', 'bootout', service_target(label)], capture_output=True, check=False
    )


def retire_legacy_service() -> None:
    """Stop and remove the render service installed under the io.github.user27182 label."""
    unload_service(daemon_mod.LEGACY_LABEL)
    daemon_mod.legacy_agent_path().unlink(missing_ok=True)
    shutil.rmtree(daemon_mod.legacy_drop_dir(), ignore_errors=True)


def service_loaded() -> bool:
    """Return whether launchd has the render service loaded in this user's session."""
    return _succeeds(['/bin/launchctl', 'print', service_target()])


def service_state() -> str:
    """Return the state launchd reports for the render service, such as running."""
    output = _run(['/bin/launchctl', 'print', service_target()])
    match = re.search(r'^\s*state = (.+)$', output, re.MULTILINE)
    return match.group(1).strip() if match else ''


def cmd_service(args: argparse.Namespace) -> int:
    """Install, remove, or report on the render service."""
    label = daemon_mod.LABEL
    target = service_target()
    path = daemon_mod.agent_path()

    if args.uninstall:
        unload_service()
        path.unlink(missing_ok=True)
        retire_legacy_service()
        print(f'removed {label}')
        return 0

    if args.install:
        helper = args.helper or shutil.which('pvql') or str(Path.home() / '.local/bin/pvql')
        # Login Items names a launch agent after its program.
        named = Path(helper).with_name(daemon_mod.SERVICE_PROGRAM)
        program = str(named) if named.is_file() else helper
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('wb') as handle:
            plistlib.dump(daemon_mod.agent_plist(program), handle, sort_keys=False)
        retire_legacy_service()
        unload_service()
        # launchd reports an I/O error when it is still tearing the old job down.
        for attempt in range(5):
            loaded = subprocess.run(
                ['/bin/launchctl', 'bootstrap', f'gui/{os.getuid()}', str(path)],
                capture_output=True,
                text=True,
                check=False,
            )
            if loaded.returncode == 0:
                break
            time.sleep(0.5 * (attempt + 1))
        if loaded.returncode != 0:
            print(f'could not load {label}: {loaded.stderr.strip()}', file=sys.stderr)
            return 1
        subprocess.run(
            ['/bin/launchctl', 'kickstart', '-k', target], capture_output=True, check=False
        )
        print(f'installed {path}')
        return 0

    if service_loaded():
        print(_run(['/bin/launchctl', 'print', target]))
    else:
        print(f'{label} is not loaded')
    return 0


def cmd_preload(_: argparse.Namespace) -> int:
    """Load PyVista and VTK so the first preview does not wait for them."""
    try:
        elapsed = preload_mod.run()
    except RenderError as error:
        print(error, file=sys.stderr)
        return 1
    print(f'PyVista and VTK are loaded ({elapsed:.1f} s)')
    return 0


def cmd_prebuild(args: argparse.Namespace) -> int:
    """Build previews ahead of time for the given files or directories."""
    config = config_mod.load()
    claimed = set(claimed_extensions(config))
    targets: list[Path] = []
    for entry in args.paths:
        path = Path(entry).expanduser()
        if path.is_dir():
            targets += [p for p in sorted(path.rglob('*')) if p.suffix.lower() in claimed]
        elif path.is_file():
            targets.append(path)
    failures = 0
    for target in targets:
        try:
            daemon_mod.produce(target, config)
        except RenderError as error:
            failures += 1
            print(f'✗ {target}: {str(error).splitlines()[0]}', file=sys.stderr)
        else:
            print(f'✓ {target}')
    print(f'{len(targets) - failures} of {len(targets)} cached')
    return 1 if failures else 0


# Apple's Quick Look extension for .ply, .stl, .obj, USD and Alembic files. Quick Look scores
# it above any other extension claiming those types, so previewing them here means standing
# it down.
NATIVE_VIEWER = 'com.apple.HydraQLPreviewExtension'


def stand_down_native_viewer(stood_down: bool) -> bool:
    """Stand macOS's own 3D previewer down for this user, or back up, with pluginkit."""
    election = 'ignore' if stood_down else 'default'
    return _succeeds(['/usr/bin/pluginkit', '-e', election, '-i', NATIVE_VIEWER])


def native_viewer_stood_down() -> bool:
    """Return whether pluginkit lists macOS's own 3D previewer as ignored."""
    return _run(['/usr/bin/pluginkit', '-m', '-i', NATIVE_VIEWER]).startswith('-')


def cmd_types(args: argparse.Namespace) -> int:
    """List the file extensions the Quick Look extension claims."""
    config = config_mod.load()
    claimed = claimed_extensions(config)
    listing = sorted({*FORMATS, *UNCLAIMED, *claimed}) if args.all else claimed
    for ext in listing:
        description = FORMATS.get(ext) or f'{ext.lstrip(".").upper()} Data'
        mark = '✓' if ext in claimed else ' '
        note = (
            ''
            if ext in claimed
            else f'  not claimed: {UNCLAIMED.get(ext, "removed in the config")}'
        )
        print(f'{mark} {ext:<10} {description:<32} {uti_for(ext)}{note}')
    # Quick Look previews these with its own viewer unless that viewer is stood down.
    native = bool(config.get('native_formats'))
    for ext, uti in NATIVE_TYPES.items():
        note = '' if native else '  not previewed: macOS previews it; set native_formats'
        print(f'{"✓" if native else "→"} {ext:<10} {NATIVE_NAMES[ext]:<32} {uti}{note}')
    for uti, description in SYSTEM_TYPES.items():
        print(f'✓ {"":<10} {description:<32} {uti}')
    if not args.all:
        print(f'\n{len(claimed)} extensions claimed. Use --all to see every known format.')
    return 0


def cmd_plist(args: argparse.Namespace) -> int:
    """Write the Info.plist files used when building the app bundle."""
    config = config_mod.load()
    extensions = claimed_extensions(config)
    # An empty --helper bakes no path in, for a bundle built where pvql is not installed.
    helper = (args.helper if args.helper is not None else shutil.which('pvql')) or None
    if args.app:
        plist_mod.write(plist_mod.app_plist(extensions, helper), args.app)
        print(args.app)
    if args.extension:
        plist_mod.write(plist_mod.extension_plist(extensions, helper), args.extension)
        print(args.extension)
    if args.thumbnail:
        plist_mod.write(plist_mod.thumbnail_plist(extensions), args.thumbnail)
        print(args.thumbnail)
    if args.holder:
        plist_mod.write(plist_mod.holder_plist(helper), args.holder)
        print(args.holder)
    return 0


def shown(value: Any) -> str:
    """Return a setting's value the way it is typed back to ``pvql config set``."""
    if isinstance(value, list):
        return ' '.join(str(v) for v in value) or '(none)'
    if isinstance(value, bool):
        return str(value).lower()
    return '(none)' if value is None else str(value)


def cmd_config(args: argparse.Namespace) -> int:
    """Show the settings, change one, or write a fresh file with discovered defaults."""
    action = getattr(args, 'action', None)
    try:
        if action in ('get', 'set', 'unset') and args.key is None:
            message = f'Name a setting to {action}: {", ".join(config_mod.KINDS)}.'
            raise config_mod.SettingError(message)
        if action == 'get':
            print(shown(config_mod.current(args.key)))
            return 0
        if action in ('set', 'unset'):
            key = config_mod.check_key(args.key)
            if action == 'set' and not args.values:
                message = f'Give a value for {key}, or use "pvql config unset {key}".'
                raise config_mod.SettingError(message)
            value = (
                config_mod.parse(key, args.values) if action == 'set' else config_mod.default(key)
            )
            config_mod.put(key, value)
            print(f'{key} = {shown(config_mod.current(key))}')
            if key.startswith('extensions.'):
                print('Run the installer again to claim the changed extensions.')
            if key == 'native_formats':
                if not stand_down_native_viewer(bool(config_mod.current(key))):
                    print(
                        "pluginkit could not change macOS's 3D previewer; see pvql doctor",
                        file=sys.stderr,
                    )
                    return 1
                if config_mod.current(key):
                    installed = installed_apps()
                    if installed and all(claims_native_types(app) for app in installed):
                        print('Select a .ply, .stl, .obj, USD or .abc file and press space.')
                    else:
                        print('Reinstall PyVista Quick Look so its extension claims these types.')
            return 0
    except config_mod.SettingError as error:
        print(error, file=sys.stderr)
        return 2
    if args.init:
        config = config_mod.load()
        config['python'] = args.python or config.get('python')
        config['pvql'] = args.helper or config.get('pvql') or shutil.which('pvql')
        path = config_mod.save(config_mod.overrides(config))
        print(f'wrote {path}')
    print(config_mod.CONFIG_PATH)
    for key in config_mod.KINDS:
        value, default = config_mod.current(key), config_mod.default(key)
        changed = '' if value == default or default is None else f'  (default {shown(default)})'
        print(f'  {key:<19} {shown(value)}{changed}')
    print(
        'Change one with "pvql config set KEY VALUE", or restore it with "pvql config unset KEY".'
    )
    return 0


def cmd_cache(args: argparse.Namespace) -> int:
    """Show, prune, or clear the preview cache."""
    if args.clear:
        print(f'removed {cache_mod.clear()} cached previews')
        return 0
    if args.prune:
        removed = cache_mod.prune(config_mod.load())
        removed += cache_mod.discard_other_versions(convert_mod.SCENE_VERSION)
        print(f'removed {removed} cached previews and descriptions of changed or deleted files')
    entries = cache_mod.entries()
    total = 0
    for entry in cache_mod.files():
        # The service may prune or land a file while this adds them up.
        with contextlib.suppress(OSError):
            total += entry.stat().st_size
    print(f'{config_mod.CACHE_DIR}\n{len(entries)} previews, {total / 1_000_000:.1f} MB')
    return 0


def _run(command: list[str]) -> str:
    """Run a command and return its combined output, or an empty string on failure."""
    completed = _completed(command)
    return '' if completed is None else (completed.stdout + completed.stderr).strip()


def _succeeds(command: list[str]) -> bool:
    """Return whether a command runs and exits cleanly."""
    completed = _completed(command)
    return completed is not None and completed.returncode == 0


def _completed(command: list[str]) -> subprocess.CompletedProcess[str] | None:
    """Run a command for its result, or None when it cannot run or does not finish."""
    try:
        return subprocess.run(command, capture_output=True, text=True, timeout=20, check=False)
    except OSError, subprocess.SubprocessError:
        return None


def finder_started() -> float | None:
    """Return when the running Finder started, or None when no Finder is running."""
    # Other users logged in through fast user switching run Finders of their own.
    pids = _run(['/usr/bin/pgrep', '-x', '-U', str(os.getuid()), 'Finder']).split()
    elapsed = _run(['/bin/ps', '-o', 'etime=', '-p', pids[0]]) if pids else ''
    match = re.fullmatch(r'(?:(\d+)-)?(?:(\d+):)?(\d+):(\d+)', elapsed)
    if not match:
        return None
    days, hours, minutes, seconds = (int(part or 0) for part in match.groups())
    return time.time() - (((days * 24 + hours) * 60 + minutes) * 60 + seconds)


def finder_is_stale() -> bool:
    """Return whether the Finder still asks for the extension by its pre-0.7.0 identifier."""
    # Retiring the old extension removes its exchange directory, which touches the folder above.
    # Nothing else writes there, so that folder's time is when the old extension went away.
    retired = daemon_mod.legacy_drop_dir().parent
    if not retired.is_dir():
        return False
    started = finder_started()
    # ps reports whole seconds, so a Finder restarted right after the retirement is not stale.
    return started is not None and started + 2 < retired.stat().st_mtime


def cmd_finder(_: argparse.Namespace) -> int:
    """Print whether the Finder must restart before it can use the extension."""
    print('stale' if finder_is_stale() else 'current')
    return 0


def cmd_doctor(_: argparse.Namespace) -> int:
    """Check that every piece of the Quick Look integration is in place."""
    config = config_mod.load()
    problems = 0

    suffix = '' if config_mod.CONFIG_PATH.is_file() else '  (using defaults)'
    print(f'config       {config_mod.CONFIG_PATH}{suffix}')
    print(f'cache        {config_mod.CACHE_DIR}')

    interpreter = config_mod.find_python(config)
    if interpreter:
        print(f'✓ python     {interpreter}')
    else:
        problems += 1
        print('✗ python     no PyVista environment; set "python" in the config file')

    executable = config_mod.resolve_pyvista(config)
    if executable and _succeeds([executable, '--help']):
        print(f'  pyvista    {executable}')
    else:
        print('  pyvista    none in this environment; still images unavailable')

    print(f'✓ formats    {len(claimed_extensions(config))} extensions claimed')
    installed = installed_apps()
    if config.get('native_formats'):
        if not native_viewer_stood_down():
            problems += 1
            print('✗ native     macOS still previews .ply, .stl, .obj, USD and .abc; set it again')
        elif not (installed and all(claims_native_types(app) for app in installed)):
            problems += 1
            print('✗ native     the installed extension predates the native formats; reinstall')
        else:
            print('✓ native     .ply, .stl, .obj, USD and .abc previewed here')

    if installed:
        for candidate in installed:
            print(f'✓ app        {candidate}')
    else:
        problems += 1
        print('✗ app        not installed; reinstall PyVista Quick Look')

    registered = _run(['/usr/bin/pluginkit', '-m', '-i', plist_mod.EXT_BUNDLE_ID])
    if registered:
        print(f'✓ extension  registered ({registered})')
    else:
        problems += 1
        print('✗ extension  not registered with pluginkit')

    thumbnails = _run(['/usr/bin/pluginkit', '-m', '-i', plist_mod.THUMB_BUNDLE_ID])
    if thumbnails:
        print(f'✓ thumbnails registered ({thumbnails})')
    else:
        problems += 1
        print('✗ thumbnails not registered with pluginkit')

    if finder_is_stale():
        problems += 1
        print('✗ finder     still asks for the old extension; run "killall Finder"')
    else:
        print('✓ finder     asks for the current extension')

    if service_loaded():
        state = service_state()
        if state == 'running':
            print(f'✓ service    {daemon_mod.LABEL} running')
        else:
            problems += 1
            print(
                f'✗ service    loaded but {state or "not running"}; see ~/Library/Logs/pvqld.log'
            )
    else:
        problems += 1
        print('✗ service    not loaded; run "pvql service --install"')

    if interpreter:
        with tempfile.TemporaryDirectory() as scratch:
            sample = Path(scratch) / 'pvql-doctor.vtk'
            sample.write_text(SMOKE_MESH)
            started = time.monotonic()
            try:
                out = daemon_mod.request_preview(sample, timeout=daemon_mod.reply_seconds(config))
            except RenderError as error:
                problems += 1
                print(f'✗ render     failed through the service\n\n{error}')
            else:
                elapsed = time.monotonic() - started
                size_kb = out.stat().st_size / 1024
                kind = 'interactive scene' if out.suffix == '.ply' else 'rendered image'
                print(f'✓ preview    {kind}, {size_kb:.0f} KB in {elapsed:.1f} s')
                out.unlink(missing_ok=True)

    print('\nall checks passed' if not problems else f'\n{problems} problem(s) found')
    return 1 if problems else 0


def claims_native_types(app: Path) -> bool:
    """Return whether an installed app's extension claims the native formats' types."""
    info = (
        plist_mod.plugins_folder(app)
        / f'{plist_mod.EXT_EXECUTABLE}.appex'
        / 'Contents'
        / 'Info.plist'
    )
    try:
        with info.open('rb') as handle:
            attributes = plistlib.load(handle)['NSExtension']['NSExtensionAttributes']
    except OSError, KeyError, plistlib.InvalidFileException:
        return False
    return set(NATIVE_TYPES.values()) <= set(attributes.get('QLSupportedContentTypes', []))


def installed_apps() -> list[Path]:
    """Return every installed copy of the app bundle, under either name it has had."""
    names = (plist_mod.APP_BUNDLE, plist_mod.LEGACY_APP_BUNDLE)
    return [d / name for d in APP_DIRS for name in names if (d / name).is_dir()]


def uninstall_targets(everything: bool) -> list[Path]:
    """Return the files and folders an uninstall removes, those that exist."""
    support = config_mod.APP_SUPPORT
    candidates = [
        *installed_apps(),
        daemon_mod.agent_path(),
        daemon_mod.legacy_agent_path(),
        config_mod.CACHE_DIR,
        support / 'venv',
        support / 'src',
        support / 'unpacked',
        daemon_mod.drop_dir(),
        daemon_mod.legacy_drop_dir(),
        SERVICE_LOG,
        config_mod.LOG_PATH,
    ]
    if everything:
        candidates.append(config_mod.CONFIG_PATH)
    return [path for path in candidates if path.exists()]


def uv_note(uv: Path) -> str:
    """Return what to tell the reader about the uv that provisioned the environment."""
    if not uv.is_relative_to(Path.home()):
        return f'uv is left in place at {uv}; remove it with whatever installed it.'
    return (
        f'uv is left in place at {uv}. The installer fetches it when it is missing, so\n'
        'remove it too if nothing else on this Mac uses it:\n'
        '    uv cache clean\n'
        '    rm -r "$(uv python dir)" "$(uv tool dir)"\n'
        f'    rm {uv} {uv.with_name("uvx")}\n'
        'That takes every tool and every Python uv manages, not only this one.'
    )


def release_tag(version: str) -> str | None:
    """Return the release tag of a version, or None for a development build."""
    return f'v{version}' if re.fullmatch(r'\d+(\.\d+)*', version) else None


def cmd_install(args: argparse.Namespace) -> int:
    """Install the app, the render service, and PyVista with the installer of this release."""
    tag = release_tag(__version__)
    if tag:
        url = f'{RELEASES}/download/{tag}/install.sh'
        print(f'==> fetching the installer of PyVista Quick Look {tag}')
    else:
        url = f'{RELEASES}/latest/download/install.sh'
        print(f'==> pvql {__version__} is not a release; fetching the latest installer')
    with tempfile.TemporaryDirectory(prefix='pvql-install-') as folder:
        script = Path(folder) / 'install.sh'
        fetched = subprocess.run(
            ['/usr/bin/curl', '-LsSf', '-o', str(script), url],
            capture_output=True,
            text=True,
            check=False,
        )
        if fetched.returncode != 0:
            reason = fetched.stderr.strip().splitlines()[-1:] or ['no response']
            print(f'pvql: could not download {url}: {reason[0]}', file=sys.stderr)
            return 1
        # Pinned, the installer sets up the app of this release rather than the newest.
        env = {**os.environ, 'PVQL_TAG': tag} if tag else dict(os.environ)
        extra = ['--prefix', args.prefix] if args.prefix else []
        return subprocess.run(['/bin/sh', str(script), *extra], env=env, check=False).returncode


def cmd_uninstall(args: argparse.Namespace) -> int:
    """Remove the app, the render service, the PyVista environment, and the helper."""
    targets = uninstall_targets(args.all)
    print('This removes')
    for target in targets:
        print(f'  {target}')
    print(f'  the render service {daemon_mod.LABEL}')
    print('  the pvql command')
    keeps_config = not args.all and config_mod.CONFIG_PATH.exists()
    if keeps_config:
        print(f'and keeps {config_mod.CONFIG_PATH}.')
    if not args.yes:
        if not sys.stdin.isatty():
            print('Run "pvql uninstall --yes" to remove them.')
            if keeps_config:
                print('Add --all to remove the configuration file as well.')
            return 1
        if keeps_config:
            print('To remove that too, answer n and run "pvql uninstall --all".')
        if input('Remove? [y/N] ').strip().lower() != 'y':
            return 1

    for executable in (plist_mod.EXT_EXECUTABLE, plist_mod.THUMB_EXECUTABLE):
        subprocess.run(['/usr/bin/pkill', '-x', executable], capture_output=True, check=False)
    for app in installed_apps():
        # Where this layout keeps the extensions, where development builds of 0.9 kept
        # them, and where 0.8 and earlier did.
        legacy_folder = plist_mod.plugins_folder(app, plist_mod.LEGACY_HOLDER_BUNDLE)
        for folder in (plist_mod.plugins_folder(app), legacy_folder, app / 'Contents' / 'PlugIns'):
            for executable in (plist_mod.EXT_EXECUTABLE, plist_mod.THUMB_EXECUTABLE):
                appex = folder / f'{executable}.appex'
                subprocess.run(
                    ['/usr/bin/pluginkit', '-r', str(appex)], capture_output=True, check=False
                )
        for bundle in (app, plist_mod.plugins_folder(app).parents[1], legacy_folder.parents[1]):
            subprocess.run([LSREGISTER, '-u', str(bundle)], capture_output=True, check=False)
    unload_service()
    retire_legacy_service()
    for path in targets:
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        else:
            path.unlink(missing_ok=True)
    for command in (['/usr/bin/qlmanage', '-r'], ['/usr/bin/qlmanage', '-r', 'cache']):
        subprocess.run(command, capture_output=True, check=False)

    print('removed', file=sys.stdout, flush=True)
    uv = Path(shutil.which('uv') or Path.home() / '.local' / 'bin' / 'uv')
    if not uv.is_file():
        print(
            'uv was not found; remove the pvql command with: uv tool uninstall pyvista-quicklook'
        )
        return 0
    subprocess.run(
        [str(uv), 'tool', 'uninstall', 'pyvista-quicklook'], capture_output=True, check=False
    )
    print('removed the pvql command')
    print(uv_note(uv))
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Return the argument parser for the ``pvql`` command."""
    parser = argparse.ArgumentParser(prog='pvql', description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)

    preview = sub.add_parser('preview', help='build a preview and print its cached path')
    preview.add_argument('path')
    preview.add_argument('-o', '--output', help='also copy the preview to this path')
    preview.add_argument('--no-cache', action='store_true', help='ignore any cached preview')
    preview.set_defaults(func=cmd_preview)

    daemon = sub.add_parser('daemon', help='run the render service in the foreground')
    daemon.set_defaults(func=cmd_daemon)

    service = sub.add_parser('service', help='install or remove the render service')
    service.add_argument('--install', action='store_true')
    service.add_argument('--uninstall', action='store_true')
    service.add_argument('--helper', help='absolute path to the pvql executable')
    service.set_defaults(func=cmd_service)

    preload = sub.add_parser('preload', help='load PyVista and VTK ahead of the first preview')
    preload.set_defaults(func=cmd_preload)

    prebuild = sub.add_parser('prebuild', help='build previews ahead of time')
    prebuild.add_argument('paths', nargs='+')
    prebuild.set_defaults(func=cmd_prebuild)

    types = sub.add_parser('types', help='list claimed file extensions')
    types.add_argument('--all', action='store_true', help='include formats that are not claimed')
    types.set_defaults(func=cmd_types)

    plist = sub.add_parser('plist', help='write Info.plist files for the app bundle')
    plist.add_argument('--app')
    plist.add_argument('--extension')
    plist.add_argument('--thumbnail')
    plist.add_argument('--holder')
    plist.add_argument('--helper', help='absolute path to the pvql executable')
    plist.set_defaults(func=cmd_plist)

    config = sub.add_parser(
        'config',
        help='show or change settings',
        description='Show every setting, or get, set, or unset one.',
        epilog='examples:\n'
        '  pvql config set max_file_size_mb 4096\n'
        '  pvql config set info_panel side\n'
        '  pvql config set window_size 1280 720\n'
        '  pvql config set extensions.add .h5\n'
        '  pvql config unset max_file_size_mb',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    config.add_argument('action', nargs='?', choices=('get', 'set', 'unset'))
    config.add_argument('key', nargs='?', help=', '.join(config_mod.KINDS))
    config.add_argument('values', nargs='*', help='the value; some settings take several')
    config.add_argument('--init', action='store_true', help='write a configuration file')
    config.add_argument('--python', help='absolute path to the interpreter that has PyVista')
    config.add_argument('--helper', help='absolute path to the pvql executable')
    config.set_defaults(func=cmd_config)

    cache = sub.add_parser('cache', help='show, prune, or clear the preview cache')
    clearing = cache.add_mutually_exclusive_group()
    clearing.add_argument('--clear', action='store_true', help='delete every cached preview')
    clearing.add_argument(
        '--prune', action='store_true', help='delete previews of files that have changed or gone'
    )
    cache.set_defaults(func=cmd_cache)

    finder = sub.add_parser('finder', help='say whether the Finder must restart to show previews')
    finder.set_defaults(func=cmd_finder)

    doctor = sub.add_parser('doctor', help='check the Quick Look integration')
    doctor.set_defaults(func=cmd_doctor)

    install = sub.add_parser(
        'install', help='install the app, the render service, and PyVista for this release'
    )
    install.add_argument('--prefix', help='folder to install the app in (default ~/Applications)')
    install.set_defaults(func=cmd_install)

    uninstall = sub.add_parser('uninstall', help='remove the app, the service, and the helper')
    uninstall.add_argument('--yes', action='store_true', help='do not ask first')
    uninstall.add_argument('--all', action='store_true', help='remove the config file too')
    uninstall.set_defaults(func=cmd_uninstall)

    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the ``pvql`` command-line interface."""
    args = build_parser().parse_args(argv)
    return args.func(args)
