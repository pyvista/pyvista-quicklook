"""Command-line interface for the PyVista Quick Look helper."""

from __future__ import annotations

import argparse
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

from . import cache as cache_mod
from . import config as config_mod
from . import daemon as daemon_mod
from . import plist as plist_mod
from . import warmup as warmup_mod
from .environment import RenderError
from .formats import FORMATS
from .formats import NATIVE_TYPES
from .formats import SYSTEM_TYPES
from .formats import UNCLAIMED
from .formats import resolve_extensions
from .formats import uti_for

# Where the app may have been installed, and where the render service writes.
APP_DIRS = (Path.home() / 'Applications', Path('/Applications'))
SERVICE_LOG = Path.home() / 'Library' / 'Logs' / 'pvqld.log'
LSREGISTER = (
    '/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework'
    '/Support/lsregister'
)

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


def retire_legacy_service() -> None:
    """Stop and remove the render service installed under the io.github.user27182 label."""
    target = f'gui/{os.getuid()}/{daemon_mod.LEGACY_LABEL}'
    subprocess.run(['/bin/launchctl', 'bootout', target], capture_output=True, check=False)
    daemon_mod.legacy_agent_path().unlink(missing_ok=True)
    shutil.rmtree(daemon_mod.legacy_drop_dir(), ignore_errors=True)


def cmd_service(args: argparse.Namespace) -> int:
    """Install, remove, or report on the render service."""
    label = daemon_mod.LABEL
    target = f'gui/{os.getuid()}/{label}'
    path = daemon_mod.agent_path()

    if args.uninstall:
        subprocess.run(['/bin/launchctl', 'bootout', target], capture_output=True, check=False)
        path.unlink(missing_ok=True)
        retire_legacy_service()
        print(f'removed {label}')
        return 0

    if args.install:
        retire_legacy_service()
        helper = args.helper or shutil.which('pvql') or str(Path.home() / '.local/bin/pvql')
        # macOS names a background item after its program until the app is registered.
        named = Path(helper).with_name('pyvista-quicklook')
        program = str(named) if named.is_file() else helper
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('wb') as handle:
            plistlib.dump(daemon_mod.agent_plist(program), handle, sort_keys=False)
        subprocess.run(['/bin/launchctl', 'bootout', target], capture_output=True, check=False)
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

    print(_run(['/bin/launchctl', 'print', target]) or f'{label} is not loaded')
    return 0


def cmd_warmup(_: argparse.Namespace) -> int:
    """Load PyVista and VTK so the first preview does not wait for them."""
    try:
        elapsed = warmup_mod.warm()
    except RenderError as error:
        print(error, file=sys.stderr)
        return 1
    print(f'PyVista and VTK are warm ({elapsed:.1f} s)')
    return 0


def cmd_warm(args: argparse.Namespace) -> int:
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
    # Quick Look previews these with its own viewer; the app only offers to open them.
    for ext, uti in NATIVE_TYPES.items():
        note = '  not previewed: macOS previews it; the app can open it'
        print(f'→ {ext:<10} {"macOS type":<32} {uti}{note}')
    for uti, description in SYSTEM_TYPES.items():
        print(f'✓ {"":<10} {description:<32} {uti}')
    if not args.all:
        print(f'\n{len(claimed)} extensions claimed. Use --all to see every known format.')
    return 0


def cmd_plist(args: argparse.Namespace) -> int:
    """Write the Info.plist files used when building the app bundle."""
    config = config_mod.load()
    extensions = claimed_extensions(config)
    helper = args.helper or shutil.which('pvql')
    if args.app:
        plist_mod.write(plist_mod.app_plist(extensions, helper), args.app)
        print(args.app)
    if args.extension:
        plist_mod.write(plist_mod.extension_plist(extensions, helper), args.extension)
        print(args.extension)
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
    """Show or clear the preview cache."""
    if args.clear:
        print(f'removed {cache_mod.clear()} cached previews')
        return 0
    entries = cache_mod.entries()
    total = sum(entry.stat().st_size for entry in cache_mod.files())
    print(f'{config_mod.CACHE_DIR}\n{len(entries)} previews, {total / 1_000_000:.1f} MB')
    return 0


def _run(command: list[str]) -> str:
    """Run a command and return its combined output, or an empty string on failure."""
    try:
        completed = subprocess.run(
            command, capture_output=True, text=True, timeout=20, check=False
        )
    except OSError, subprocess.SubprocessError:
        return ''
    return (completed.stdout + completed.stderr).strip()


def _succeeds(command: list[str]) -> bool:
    """Return whether a command runs and exits cleanly."""
    try:
        completed = subprocess.run(command, capture_output=True, timeout=20, check=False)
    except OSError, subprocess.SubprocessError:
        return False
    return completed.returncode == 0


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

    if finder_is_stale():
        problems += 1
        print('✗ finder     still asks for the old extension; run "killall Finder"')
    else:
        print('✓ finder     asks for the current extension')

    target = f'gui/{os.getuid()}/{daemon_mod.LABEL}'
    if _run(['/bin/launchctl', 'print', target]):
        print(f'✓ service    {daemon_mod.LABEL} loaded')
    else:
        problems += 1
        print('✗ service    not loaded; run "pvql service --install"')

    if interpreter:
        with tempfile.TemporaryDirectory() as scratch:
            sample = Path(scratch) / 'pvql-doctor.vtk'
            sample.write_text(SMOKE_MESH)
            started = time.monotonic()
            try:
                out = daemon_mod.request_preview(sample, timeout=90)
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

    subprocess.run(
        ['/usr/bin/pkill', '-x', plist_mod.EXT_EXECUTABLE], capture_output=True, check=False
    )
    for app in installed_apps():
        appex = app / 'Contents' / 'PlugIns' / f'{plist_mod.EXT_EXECUTABLE}.appex'
        subprocess.run(['/usr/bin/pluginkit', '-r', str(appex)], capture_output=True, check=False)
        subprocess.run([LSREGISTER, '-u', str(app)], capture_output=True, check=False)
    target = f'gui/{os.getuid()}/{daemon_mod.LABEL}'
    subprocess.run(['/bin/launchctl', 'bootout', target], capture_output=True, check=False)
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

    warmup = sub.add_parser('warmup', help='load PyVista and VTK ahead of the first preview')
    warmup.set_defaults(func=cmd_warmup)

    warm = sub.add_parser('warm', help='build previews ahead of time')
    warm.add_argument('paths', nargs='+')
    warm.set_defaults(func=cmd_warm)

    types = sub.add_parser('types', help='list claimed file extensions')
    types.add_argument('--all', action='store_true', help='include formats that are not claimed')
    types.set_defaults(func=cmd_types)

    plist = sub.add_parser('plist', help='write Info.plist files for the app bundle')
    plist.add_argument('--app')
    plist.add_argument('--extension')
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

    cache = sub.add_parser('cache', help='show or clear the preview cache')
    cache.add_argument('--clear', action='store_true')
    cache.set_defaults(func=cmd_cache)

    finder = sub.add_parser('finder', help='say whether the Finder must restart to show previews')
    finder.set_defaults(func=cmd_finder)

    doctor = sub.add_parser('doctor', help='check the Quick Look integration')
    doctor.set_defaults(func=cmd_doctor)

    uninstall = sub.add_parser('uninstall', help='remove the app, the service, and the helper')
    uninstall.add_argument('--yes', action='store_true', help='do not ask first')
    uninstall.add_argument('--all', action='store_true', help='remove the config file too')
    uninstall.set_defaults(func=cmd_uninstall)

    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the ``pvql`` command-line interface."""
    args = build_parser().parse_args(argv)
    return args.func(args)
