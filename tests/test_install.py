"""The source installer's handling of uv and of upgrades, run against stand-in commands."""

from __future__ import annotations

from pathlib import Path
import re
import shlex
import subprocess
import tomllib

import pytest

ROOT = Path(__file__).parents[1]
INSTALLER = ROOT / 'scripts' / 'install-from-source.sh'
SCRIPT = INSTALLER.read_text()
UV_MIN = re.search(r'^UV_MIN="([\d.]+)"$', SCRIPT, re.MULTILINE).group(1)
PYTHON_VERSION = re.search(
    r'^PYTHON_VERSION="\$\{PVQL_PYTHON:-([\d.]+)\}"$', SCRIPT, re.MULTILINE
).group(1)
REACHED_CONFIGURATION = '==> recording configuration'

STAND_IN_UV = Path(__file__).parent / 'data' / 'stand-in-uv.sh'


def run_installer(tmp_path, version, update_to=''):
    """Run the installer with a uv reporting ``version``; return the result and uv's calls."""
    assert SCRIPT.index('config --init') < SCRIPT.index('lsregister'), (
        'the stand-in pvql must stop the installer before it registers anything'
    )
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    uv = bin_dir / 'uv'
    uv.write_bytes(STAND_IN_UV.read_bytes())
    pvql = bin_dir / 'pvql'
    pvql.write_text('#!/bin/sh\nexit 1\n')
    for tool in (uv, pvql):
        tool.chmod(0o755)
    (tmp_path / 'uv-version').write_text(version)
    log = tmp_path / 'uv.log'
    log.touch()
    env = {
        'HOME': str(tmp_path / 'home'),
        'PATH': f'{bin_dir}:/usr/bin:/bin',
        'STAND_IN_LOG': str(log),
        'STAND_IN_VERSION': str(tmp_path / 'uv-version'),
        'STAND_IN_UPDATE_TO': update_to,
        'STAND_IN_EXCLUDES': str(tmp_path / 'excludes'),
    }
    result = subprocess.run(
        [str(INSTALLER), '--app', str(tmp_path / 'App.app'), '--skip-helper'],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    return result, log.read_text()


def test_installer_resolves_without_stock_vtk(tmp_path):
    """The PyVista environment is installed with vtk excluded from the resolve."""
    result, log = run_installer(tmp_path, '0.11.14')
    assert REACHED_CONFIGURATION in result.stdout, result.stderr
    assert any(line.startswith('pip install') for line in log.splitlines())
    assert (tmp_path / 'excludes').read_text().split() == ['vtk']


@pytest.mark.parametrize('version', ['0.10', '0.10.0', '0.10.1', '0.11.14', '1.0.0'])
def test_installer_keeps_a_new_enough_uv(tmp_path, version):
    """A uv at or above the floor is used as it is."""
    result, log = run_installer(tmp_path, version)
    assert REACHED_CONFIGURATION in result.stdout, result.stderr
    assert 'self update' not in log


@pytest.mark.parametrize('version', ['0.2.0', '0.9.7', '0.9.20'])
def test_installer_updates_an_old_uv(tmp_path, version):
    """A uv below the floor is updated, and the install goes on."""
    result, log = run_installer(tmp_path, version, update_to='0.13.0')
    assert f'==> updating uv {version}, which is older than {UV_MIN}' in result.stdout
    assert 'self update' in log
    assert REACHED_CONFIGURATION in result.stdout, result.stderr


@pytest.mark.parametrize(('version', 'shown'), [('0.9.7', '0.9.7'), ('', 'unknown')])
def test_installer_stops_on_a_uv_that_cannot_update(tmp_path, version, shown):
    """A uv that stays below the floor stops the install before anything is created."""
    result, log = run_installer(tmp_path, version)
    assert result.returncode == 1
    assert 'error: cannot self-update this uv' in result.stderr
    uv = tmp_path / 'bin' / 'uv'
    assert f'uv {shown} at {uv} is older than {UV_MIN}' in result.stderr
    assert 'venv' not in log
    assert not (tmp_path / 'home').exists()


def test_project_requires_the_installer_uv():
    """pyproject.toml asks for the same uv as the installer."""
    tool_uv = tomllib.loads((ROOT / 'pyproject.toml').read_text())['tool']['uv']
    assert tool_uv['required-version'] == f'>={UV_MIN}'


def test_lockfile_excludes_stock_vtk():
    """The committed lock records the exclusion and holds no vtk."""
    lock = tomllib.loads((ROOT / 'uv.lock').read_text())
    assert lock['manifest']['excludes'] == ['vtk']
    assert 'vtk' not in {package['name'] for package in lock['package']}


def test_readme_names_the_installer_python():
    """Every Python the README names is the one the installer and project use."""
    readme = (ROOT / 'README.md').read_text()
    assert set(re.findall(r'Python,? (3\.\d+)', readme)) == {PYTHON_VERSION}
    project = tomllib.loads((ROOT / 'pyproject.toml').read_text())['project']
    assert project['requires-python'].split(',')[0] == f'>={PYTHON_VERSION}'


def script_lines(first, last):
    """Return the installer's lines from the one starting ``first`` through ``last``."""
    start = SCRIPT.index(first)
    return SCRIPT[start : SCRIPT.index(last, start) + len(last)]


def run_strict(script, **kwargs):
    """Run a piece of the installer under its own shell options, then report reaching the end."""
    return subprocess.run(
        ['/bin/bash', '-c', f'set -euo pipefail\n{script}echo reached the end\n'],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
        **kwargs,
    )


def ask_to_restart_finder(tmp_path, answer, killall_status=0):
    """Answer the Finder prompt, or leave it unanswered; return the result and killall's log."""
    calls = tmp_path / 'killall.log'
    killall = tmp_path / 'killall'
    killall.write_text(
        f'#!/bin/sh\necho "$@" >> {shlex.quote(str(calls))}\nexit {killall_status}\n'
    )
    killall.chmod(0o755)
    function = script_lines('restart_finder() {', '\n}\n')
    function = function.replace('/usr/bin/killall', shlex.quote(str(killall)))
    typed = '3</dev/null' if answer is None else f'3<<< {shlex.quote(answer)}'
    result = run_strict(f'{function}restart_finder {typed}\n')
    return result, calls.read_text() if calls.exists() else ''


@pytest.mark.parametrize('answer', ['y', 'Y', 'yes'])
def test_finder_is_restarted_on_yes(tmp_path, answer):
    """A yes restarts the Finder, after the risk is explained."""
    result, calls = ask_to_restart_finder(tmp_path, answer)
    assert 'Make sure no files are currently being moved or copied.' in result.stdout
    assert calls == 'Finder\n'
    assert result.stdout.endswith('reached the end\n'), result.stderr


@pytest.mark.parametrize('answer', ['n', '', 'sure', None])
def test_finder_is_left_running_otherwise(tmp_path, answer):
    """Any other answer, or none at all, leaves the Finder alone and says how to finish."""
    result, calls = ask_to_restart_finder(tmp_path, answer)
    assert calls == ''
    assert 'Restart it later with "killall Finder"' in result.stdout
    assert result.stdout.endswith('reached the end\n'), result.stderr


def test_a_failed_finder_restart_does_not_stop_the_install(tmp_path):
    """The install goes on when killall fails, as it does with no Finder running."""
    result, calls = ask_to_restart_finder(tmp_path, 'y', killall_status=1)
    assert calls == 'Finder\n'
    assert result.stdout.endswith('reached the end\n'), result.stderr


def check_finder(tmp_path, helper_body):
    """Run the installer's Finder check, with no terminal, against a stand-in pvql."""
    helper = tmp_path / 'pvql'
    helper.write_text(f'#!/bin/sh\n{helper_body}\n')
    helper.chmod(0o755)
    check = script_lines('if [[ "$("$HELPER" finder', '\nfi\n')
    return run_strict(
        f'HELPER={shlex.quote(str(helper))}\nrestart_finder() {{ echo asked; }}\n{check}',
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )


def test_without_a_terminal_the_installer_says_how_to_finish(tmp_path):
    """With no terminal to ask on, a stale Finder gets the remedy and the install goes on."""
    result = check_finder(tmp_path, 'echo stale')
    assert 'asked' not in result.stdout
    assert 'to finish installing' in result.stdout
    assert result.stdout.endswith('reached the end\n'), result.stderr


@pytest.mark.parametrize('helper_body', ['echo current', 'exit 1'])
def test_a_current_finder_is_left_alone(tmp_path, helper_body):
    """A Finder that needs no restart, or a check that fails, asks nothing and says nothing."""
    result = check_finder(tmp_path, helper_body)
    assert result.stdout == 'reached the end\n', result.stderr


def test_the_finder_is_checked_after_the_old_extension_is_retired():
    """Installing the service retires the old extension, which the Finder check reads."""
    assert SCRIPT.index('service --install') < SCRIPT.index('"$HELPER" finder')


def test_upgrades_from_before_0_7_are_retired_by_python_3_17():
    """The code that upgrades installs from before 0.7.0 goes once the app moves to Python 3.17."""
    assert tuple(int(part) for part in PYTHON_VERSION.split('.')) < (3, 17), (
        'Upgrades from before 0.7.0 are long past; remove the code that handles them:\n'
        '- scripts/install-from-source.sh: LEGACY_BUNDLE_NAME, LEGACY_APP and its removal,\n'
        '  restart_finder and the Finder check that calls it\n'
        '- pyvista_quicklook/cli.py: retire_legacy_service and its calls, the old bundle\n'
        '  name in installed_apps, the legacy paths in uninstall_targets, finder_started,\n'
        '  finder_is_stale, cmd_finder and its parser, the finder line in cmd_doctor\n'
        '- pyvista_quicklook/daemon.py: LEGACY_LABEL, legacy_agent_path, legacy_drop_dir\n'
        '- pyvista_quicklook/plist.py: LEGACY_APP_BUNDLE, LEGACY_APP_BUNDLE_ID,\n'
        '  LEGACY_EXT_BUNDLE_ID\n'
        '- tests/test_install.py: the Finder restart and check tests, script_lines,\n'
        '  run_strict, ask_to_restart_finder, check_finder, and this test\n'
        '- tests/test_cli.py: the finder tests\n'
        '- README.md: the Finder troubleshooting entry\n'
        '- tests/test_pyvista_quicklook.py: test_uninstall_removes_a_bundle_under_the_old_name,\n'
        '  test_uninstall_removes_the_service_under_the_old_label, and the old service in\n'
        '  test_service_install_runs_the_daemon_under_the_package_name\n'
        '- tests/test_daemon.py: the legacy assertions in\n'
        '  test_exchange_locations_are_in_the_extension_containers\n'
        '- tests/conftest.py: the legacy_agent_path and legacy_drop_dir stand-ins'
    )


def sed_expression(producer):
    """Return the sed expression the installer applies to a command's listing."""
    pattern = re.escape(producer) + r".*?\n?\s*\|\s*sed -n '([^']+)'"
    return re.search(pattern, SCRIPT, re.DOTALL).group(1)


def extracted(producer, listing):
    """Return the paths the installer's sed expression picks out of a listing."""
    result = subprocess.run(
        ['sed', '-n', sed_expression(producer)],
        input=listing,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.splitlines()


def test_the_installer_picks_app_records_out_of_the_launch_services_dump():
    """Only this app's bundles, by their exact names, are read from lsregister's listing."""
    listing = (
        '\tpath:                       /Users/x/Applications/PyVista Quick Look.app (0x2b28)\n'
        '\tpath:                       /Users/x/Applications/PyVista Quick Look.app'
        '/Contents/Library/PyVista Quick Look Extensions.app (0x2b30)\n'
        '\tpath:                       /Users/x/src/build/PyVista Quick Look.app (0x2c00)\n'
        '\tpath:                       /Users/x/PyVista Quick Look notes/Other.app (0x2d00)\n'
        '\tpath:                       /Users/x/Applications/PyVista Quick Look.app'
        '/Contents/PlugIns/PyVistaQuickLookExtension.appex (0x2b2c)\n'
    )
    app = '/Users/x/Applications/PyVista Quick Look.app'
    assert extracted('"$LSREGISTER" -dump', listing) == [
        app,
        f'{app}/Contents/Library/PyVista Quick Look Extensions.app',
        '/Users/x/src/build/PyVista Quick Look.app',
    ]


def test_the_installer_picks_extension_records_out_of_the_pluginkit_listing():
    """Every registered copy of the extensions is read from pluginkit's listing."""
    listing = (
        '+    org.pyvista.PyVistaQuickLook.QuickLook(0.9.0)\n'
        '\t            Path = /Users/x/Applications/PyVista Quick Look.app'
        '/Contents/PlugIns/PyVistaQuickLookExtension.appex\n'
        '\t            UUID = 00523111-4007-42E5-B23E-BBD5C7E2EF9D\n'
        '+    org.pyvista.PyVistaQuickLook.Thumbnail(0.9.0)\n'
        '\t            Path = /Users/x/src/build/PyVista Quick Look.app/Contents/Library'
        '/PyVista Quick Look Extensions.app/Contents/PlugIns/PyVistaQuickLookThumbnail.appex\n'
        '\t            Path = /Applications/GarageBand.app/Contents/PlugIns/GarageBandQL.appex\n'
    )
    installed = '/Users/x/Applications/PyVista Quick Look.app/Contents/PlugIns'
    built = '/Users/x/src/build/PyVista Quick Look.app/Contents/Library/PyVista Quick Look Extensions'
    assert extracted('/usr/bin/pluginkit -mAvvv', listing) == [
        f'{installed}/PyVistaQuickLookExtension.appex',
        f'{built}.app/Contents/PlugIns/PyVistaQuickLookThumbnail.appex',
    ]


def test_every_part_names_the_extensions_app_as_plist_does():
    """The Swift helper and both scripts find the extensions where plist.py puts them."""
    from pyvista_quicklook import plist

    for source in (
        ROOT / 'macos' / 'Shared' / 'Helper.swift',
        ROOT / 'scripts' / 'build.sh',
        INSTALLER,
    ):
        assert plist.HOLDER_BUNDLE in source.read_text() or (
            '$BUNDLE_NAME Extensions' in source.read_text()
        ), source.name
