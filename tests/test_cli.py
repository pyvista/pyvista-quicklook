"""Tests for the ``pvql`` subcommands, with every system command replaced."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import plistlib
import runpy
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from pyvista_quicklook import cli
from pyvista_quicklook import config
from pyvista_quicklook import daemon
from pyvista_quicklook import plist
from pyvista_quicklook.environment import RenderError


def test_main_dispatches_to_the_subcommand(capsys, home):
    """``main`` parses its arguments and returns the subcommand's exit status."""
    assert cli.main(['cache']) == 0
    assert '0 previews' in capsys.readouterr().out


def test_module_runs_the_cli(capsys, monkeypatch, home):
    """``python -m pyvista_quicklook`` exits with the CLI's status."""
    monkeypatch.setattr(sys, 'argv', ['pvql', 'cache'])
    monkeypatch.delitem(sys.modules, 'pyvista_quicklook.__main__', raising=False)
    with pytest.raises(SystemExit) as exit_info:
        runpy.run_module('pyvista_quicklook', run_name='__main__')
    assert exit_info.value.code == 0
    assert 'previews' in capsys.readouterr().out


def test_daemon_runs_the_service(monkeypatch):
    """The daemon subcommand hands over to the service loop."""
    monkeypatch.setattr(cli.daemon_mod, 'serve', lambda: 7)
    assert cli.cmd_daemon(argparse.Namespace()) == 7


def test_preview_bypasses_the_cache_and_copies_the_output(capsys, monkeypatch, tmp_path):
    """``--no-cache`` reaches the producer and ``--output`` receives a copy."""
    built = tmp_path / 'scene.ply'
    built.write_text('ply')
    seen = []
    monkeypatch.setattr(cli.config_mod, 'load', lambda: dict(config.DEFAULTS))
    monkeypatch.setattr(
        cli.daemon_mod, 'produce', lambda path, cfg: seen.append(cfg['cache']) or built
    )
    copy = tmp_path / 'copy.ply'
    args = argparse.Namespace(path='mesh.vtu', no_cache=True, output=str(copy))
    assert cli.cmd_preview(args) == 0
    assert seen == [False]
    assert copy.read_text() == 'ply'
    assert capsys.readouterr().out.strip() == str(copy)


def test_service_uninstall_unloads_and_removes_old_agents(capsys, commands, home):
    """Uninstalling unloads the agent and deletes it, with what earlier installs wrote."""
    (home / 'agent.plist').write_text('x')
    (home / 'old-agent.plist').write_text('x')
    cli.daemon_mod.legacy_drop_dir().mkdir(parents=True)
    args = argparse.Namespace(install=False, uninstall=True, helper=None)
    assert cli.cmd_service(args) == 0
    assert not (home / 'agent.plist').exists()
    assert not (home / 'old-agent.plist').exists()
    assert not cli.daemon_mod.legacy_drop_dir().exists()
    assert sum('bootout' in command for command in commands) == 2
    assert 'removed' in capsys.readouterr().out


def test_service_install_writes_the_agent_and_starts_it(commands, home):
    """Installing writes the agent for the helper, loads it and starts it."""
    args = argparse.Namespace(install=True, uninstall=False, helper='/opt/pvql')
    assert cli.cmd_service(args) == 0
    with (home / 'agent.plist').open('rb') as handle:
        agent = plistlib.load(handle)
    assert agent['ProgramArguments'] == ['/opt/pvql', 'daemon']
    # Installed under its own name next to pvql, the service runs under that name.
    named = home / 'bin' / daemon.SERVICE_PROGRAM
    named.parent.mkdir()
    named.write_text('')
    args = argparse.Namespace(install=True, uninstall=False, helper=str(home / 'bin' / 'pvql'))
    assert cli.cmd_service(args) == 0
    with (home / 'agent.plist').open('rb') as handle:
        assert plistlib.load(handle)['ProgramArguments'] == [str(named), 'daemon']
    assert any(c[:2] == ['/bin/launchctl', 'bootstrap'] for c in commands)
    assert any('kickstart' in command for command in commands)


def test_service_install_reports_a_job_launchd_refuses(capsys, monkeypatch, home):
    """A bootstrap launchd keeps refusing is reported with its reason, after a few tries."""
    monkeypatch.setattr(cli.time, 'sleep', lambda seconds: None)

    def run(command, **kwargs):
        if command[1:2] == ['bootstrap']:
            return subprocess.CompletedProcess(command, 5, '', 'Input/output error\n')
        return subprocess.CompletedProcess(command, 0, '', '')

    monkeypatch.setattr(cli.subprocess, 'run', run)
    args = argparse.Namespace(install=True, uninstall=False, helper='/opt/pvql')
    assert cli.cmd_service(args) == 1
    assert 'Input/output error' in capsys.readouterr().err


def test_service_reports_a_job_that_is_not_loaded(capsys, monkeypatch):
    """With no flags, the status of the job is printed, or that it is not loaded."""
    monkeypatch.setattr(cli, '_run', lambda command: 'Bad request.')
    monkeypatch.setattr(cli, '_succeeds', lambda command: False)
    assert cli.cmd_service(argparse.Namespace(install=False, uninstall=False)) == 0
    assert 'is not loaded' in capsys.readouterr().out


def test_preload_reports_the_time_taken(capsys, monkeypatch):
    """A successful preload says how long it took."""
    monkeypatch.setattr(cli.preload_mod, 'run', lambda: 1.25)
    assert cli.cmd_preload(argparse.Namespace()) == 0
    assert '1.2 s' in capsys.readouterr().out


def test_preload_reports_a_failure(capsys, monkeypatch):
    """A failed preload is reported on stderr with a failing status."""

    def fail():
        message = 'no interpreter'
        raise RenderError(message)

    monkeypatch.setattr(cli.preload_mod, 'run', fail)
    assert cli.cmd_preload(argparse.Namespace()) == 1
    assert 'no interpreter' in capsys.readouterr().err


def test_prebuild_counts_failures_and_named_files(capsys, monkeypatch, tmp_path):
    """Named files are preloaded as given, and each failure is reported in one line."""
    good = tmp_path / 'good.vtu'
    bad = tmp_path / 'bad.vtu'
    for path in (good, bad):
        path.write_text('x')

    def produce(target, cfg):
        if target == bad:
            message = 'unreadable\nmore detail'
            raise RenderError(message)
        return target

    monkeypatch.setattr(cli.config_mod, 'load', lambda: dict(config.DEFAULTS))
    monkeypatch.setattr(cli.daemon_mod, 'produce', produce)
    args = argparse.Namespace(paths=[str(good), str(bad), str(tmp_path / 'missing.vtu')])
    assert cli.cmd_prebuild(args) == 1
    captured = capsys.readouterr()
    assert '1 of 2 cached' in captured.out
    assert 'unreadable' in captured.err
    assert 'more detail' not in captured.err


def test_types_all_lists_unclaimed_formats(capsys, monkeypatch):
    """``--all`` lists formats that are not claimed, with the reason."""
    removed = {**config.DEFAULTS, 'extensions': {'add': [], 'remove': ['.vtu']}}
    monkeypatch.setattr(cli.config_mod, 'load', lambda: removed)
    assert cli.cmd_types(argparse.Namespace(all=True)) == 0
    rows = capsys.readouterr().out.splitlines()
    vtu = next(row for row in rows if row[2:].startswith('.vtu '))
    assert vtu.startswith(' ')
    assert vtu.endswith('not claimed: removed in the config')
    assert any(row.startswith('✓ .vtp') for row in rows)
    assert not any('extensions claimed' in row for row in rows)


def test_plist_writes_nothing_without_targets(capsys, monkeypatch):
    """Without a target no file is written."""
    monkeypatch.setattr(cli.config_mod, 'load', lambda: dict(config.DEFAULTS))
    args = argparse.Namespace(
        app=None, extension=None, thumbnail=None, holder=None, helper='/bin/pvql'
    )
    assert cli.cmd_plist(args) == 0
    assert capsys.readouterr().out == ''


def config_args(action=None, key=None, *values):
    """Return the arguments ``pvql config`` parses for an action."""
    return argparse.Namespace(action=action, key=key, values=list(values), init=False)


def test_config_lists_every_setting_and_marks_the_changed_ones(capsys, home):
    """Every setting is listed with its value, and a changed one with its default too."""
    cli.cmd_config(config_args())
    out = capsys.readouterr().out
    assert 'max_file_size_mb    2048\n' in out
    assert 'pvql config set KEY VALUE' in out
    config.CONFIG_PATH.parent.mkdir(parents=True)
    config.CONFIG_PATH.write_text(json.dumps({'timeout': 5}))
    cli.cmd_config(config_args())
    assert 'timeout             5  (default 60)' in capsys.readouterr().out


@pytest.mark.parametrize(
    ('key', 'values', 'stored', 'shown'),
    [
        ('max_file_size_mb', ['4096'], 4096, '4096'),
        ('max_file_size_mb', ['0'], 0, '0'),
        ('timeout', ['90'], 90, '90'),
        ('timeout', ['2.5'], 2.5, '2.5'),
        ('interactive', ['no'], False, 'false'),
        ('force_interactive', ['yes'], True, 'true'),
        ('info_panel', ['side'], 'side', 'side'),
        ('window_size', ['1280', '720'], [1280, 720], '1280 720'),
        (
            'extra_args',
            ['--show-edges', '--opacity', '0.5'],
            ['--show-edges', '--opacity', '0.5'],
            '--show-edges --opacity 0.5',
        ),
        ('background', ['white'], 'white', 'white'),
    ],
)
def test_config_set_stores_a_checked_value(capsys, home, key, values, stored, shown):
    """A value is read for its setting, stored in the file, and read back by get."""
    assert cli.cmd_config(config_args('set', key, *values)) == 0
    assert capsys.readouterr().out == f'{key} = {shown}\n'
    assert json.loads(config.CONFIG_PATH.read_text())[key] == stored
    assert config.MIRROR_PATH.read_text() == config.CONFIG_PATH.read_text()
    assert config.load()[key] == stored
    cli.cmd_config(config_args('get', key))
    assert capsys.readouterr().out == f'{shown}\n'


@pytest.mark.parametrize(
    ('key', 'values', 'complaint'),
    [
        ('max_file_size', ['4096'], 'Unknown setting'),
        ('max_file_size_mb', ['big'], 'whole number'),
        ('max_file_size_mb', ['-1'], 'whole number'),
        ('timeout', ['0'], 'number of seconds'),
        ('timeout', ['nan'], 'number of seconds'),
        ('timeout', ['inf'], 'number of seconds'),
        ('interactive', ['maybe'], 'true or false'),
        ('force_interactive', ['sometimes'], 'true or false'),
        ('info_panel', ['left'], 'automatic, side, below, compact, hidden'),
        ('window_size', ['1280'], 'width and a height'),
        ('python', ['/no/such/python'], 'not an executable'),
        ('timeout', [], 'Give a value'),
        ('timeout', ['1', '2'], 'takes one value'),
        (None, [], 'Name a setting to set: python,'),
    ],
)
def test_config_set_refuses_what_it_cannot_store(capsys, home, key, values, complaint):
    """A bad key or value is explained, and the file is left as it was."""
    assert cli.cmd_config(config_args('set', key, *values)) == 2
    assert complaint in capsys.readouterr().err
    assert not config.CONFIG_PATH.exists()


def test_config_unset_restores_the_default_and_tidies_the_file(capsys, home):
    """Unsetting removes the key, and setting a default stores nothing."""
    cli.cmd_config(config_args('set', 'max_file_size_mb', '4096'))
    cli.cmd_config(config_args('set', 'log', 'true'))
    assert cli.cmd_config(config_args('unset', 'max_file_size_mb')) == 0
    assert 'max_file_size_mb = 2048' in capsys.readouterr().out
    assert json.loads(config.CONFIG_PATH.read_text()) == {'log': True}
    cli.cmd_config(config_args('set', 'log', 'false'))
    assert json.loads(config.CONFIG_PATH.read_text()) == {}


def test_config_set_keeps_settings_it_does_not_touch(home):
    """Other keys in the file, including ones set by hand, survive a set."""
    config.CONFIG_PATH.parent.mkdir(parents=True)
    config.CONFIG_PATH.write_text(json.dumps({'python': '/x/python', 'timeout': 5}))
    cli.cmd_config(config_args('set', 'info_panel', 'below'))
    assert json.loads(config.CONFIG_PATH.read_text()) == {
        'python': '/x/python',
        'timeout': 5,
        'info_panel': 'below',
    }


def test_config_set_changes_claimed_extensions(capsys, home):
    """Extensions are added and removed by name, normalised, with a note to reinstall."""
    assert cli.cmd_config(config_args('set', 'extensions.add', 'H5', '.Foo')) == 0
    out = capsys.readouterr().out
    assert out.startswith('extensions.add = .h5 .foo\n')
    assert 'installer again' in out
    assert json.loads(config.CONFIG_PATH.read_text()) == {'extensions': {'add': ['.h5', '.foo']}}
    assert config.load()['extensions'] == {'add': ['.h5', '.foo'], 'remove': []}
    cli.cmd_config(config_args('unset', 'extensions.add'))
    assert json.loads(config.CONFIG_PATH.read_text()) == {}


def test_config_set_native_formats_stands_the_macos_viewer_down(capsys, home, monkeypatch):
    """Native formats on ignores macOS's 3D previewer, off restores it; both say to reinstall."""
    commands = []
    monkeypatch.setattr(cli, '_succeeds', lambda command: commands.append(command) or True)
    assert cli.cmd_config(config_args('set', 'native_formats', 'true')) == 0
    assert 'press space to try it' in capsys.readouterr().out
    assert commands == [['/usr/bin/pluginkit', '-e', 'ignore', '-i', cli.NATIVE_VIEWER]]
    assert cli.cmd_config(config_args('unset', 'native_formats')) == 0
    assert commands[-1] == ['/usr/bin/pluginkit', '-e', 'default', '-i', cli.NATIVE_VIEWER]


def test_config_set_native_formats_reports_a_failed_election(capsys, home, monkeypatch):
    """When pluginkit cannot change the viewer, the command says so and fails."""
    monkeypatch.setattr(cli, '_succeeds', lambda command: False)
    assert cli.cmd_config(config_args('set', 'native_formats', 'true')) == 1
    assert 'pluginkit could not' in capsys.readouterr().err


def test_config_set_refuses_a_broken_file(capsys, home):
    """A file that is not JSON is never overwritten, so nothing in it is lost."""
    config.CONFIG_PATH.parent.mkdir(parents=True)
    config.CONFIG_PATH.write_text('{"timeout": 5,')
    assert cli.cmd_config(config_args('set', 'timeout', '9')) == 2
    assert 'not valid JSON' in capsys.readouterr().err
    assert config.CONFIG_PATH.read_text() == '{"timeout": 5,'
    config.CONFIG_PATH.write_text('[1]')
    assert cli.cmd_config(config_args('set', 'timeout', '9')) == 2
    assert 'JSON object' in capsys.readouterr().err


def test_config_get_without_a_key_names_the_settings(capsys, home):
    """Asking for no setting lists the ones there are, rather than an unknown "None"."""
    assert cli.cmd_config(config_args('get')) == 2
    err = capsys.readouterr().err
    assert err.startswith('Name a setting to get: python,')
    assert 'None' not in err


def test_config_stores_an_interpreter_as_an_absolute_path(home, tmp_path, monkeypatch):
    """A relative interpreter is stored absolute, without following a venv's link."""
    venv = tmp_path / 'project' / '.venv' / 'bin'
    venv.mkdir(parents=True)
    (venv / 'python').symlink_to(sys.executable)
    monkeypatch.chdir(tmp_path / 'project')
    assert cli.cmd_config(config_args('set', 'python', '.venv/bin/python')) == 0
    assert json.loads(config.CONFIG_PATH.read_text())['python'] == str(venv / 'python')


def test_config_set_survives_unknown_nested_keys(home):
    """A key added to a section by hand does not stop another from being set."""
    config.CONFIG_PATH.parent.mkdir(parents=True)
    config.CONFIG_PATH.write_text(json.dumps({'extensions': {'legacy': 1}}))
    assert cli.cmd_config(config_args('set', 'extensions.add', '.h5')) == 0
    assert json.loads(config.CONFIG_PATH.read_text()) == {
        'extensions': {'legacy': 1, 'add': ['.h5']}
    }


def test_config_parses_from_the_command_line(home, capsys):
    """The parser routes get, set, and unset with their key and values."""
    assert cli.main(['config', 'set', 'window_size', '800', '600']) == 0
    assert cli.main(['config', 'get', 'window_size']) == 0
    assert capsys.readouterr().out.endswith('800 600\n')


def test_config_takes_a_theme_name_or_a_saved_theme(home, tmp_path):
    """A theme is a name, kept as given, or a JSON file, kept as an absolute path."""
    assert config.parse('theme', ['document']) == 'document'
    saved = tmp_path / 'mine.json'
    saved.write_text('{}')
    assert config.parse('theme', [str(saved)]) == str(saved)
    with pytest.raises(config.SettingError, match='theme name or a theme saved as JSON'):
        config.parse('theme', [str(tmp_path / 'missing.json')])


def test_run_and_succeeds_survive_a_missing_program(tmp_path):
    """A program that cannot be started gives no output and does not succeed."""
    missing = str(tmp_path / 'no-such-program')
    assert cli._run([missing]) == ''
    assert cli._succeeds([missing]) is False
    assert cli._run([sys.executable, '-c', 'print("hi")']) == 'hi'
    assert cli._succeeds([sys.executable, '-c', 'raise SystemExit(3)']) is False


@pytest.fixture
def doctor(home, monkeypatch):
    """Return a hook that sets what each doctor check finds."""

    def setup(*, healthy, preview=None, pyvista_runs=None, loaded=None, state=None):
        interpreter = sys.executable if healthy else None
        runs = healthy if pyvista_runs is None else pyvista_runs
        loaded = healthy if loaded is None else loaded
        state = ('running' if healthy else '') if state is None else state
        monkeypatch.setattr(cli.config_mod, 'find_python', lambda cfg: interpreter)
        monkeypatch.setattr(cli.config_mod, 'resolve_pyvista', lambda cfg: interpreter)
        monkeypatch.setattr(
            cli, '_succeeds', lambda command: loaded if command[0].endswith('launchctl') else runs
        )
        monkeypatch.setattr(
            cli,
            '_run',
            lambda command: (
                f'{{\n\tstate = {state}\n}}'
                if command[0].endswith('launchctl')
                else ('loaded' if healthy else '')
            ),
        )
        if healthy:
            (home / 'Applications' / plist.APP_BUNDLE).mkdir(parents=True)
        monkeypatch.setattr(cli.daemon_mod, 'request_preview', preview)

    return setup


def test_doctor_passes_a_healthy_installation(capsys, doctor, tmp_path):
    """Every check passes, the smoke render included, and the result is cleaned up."""
    rendered = tmp_path / 'smoke.ply'

    def preview(sample, timeout):
        assert 'POLYGONS' in sample.read_text()
        rendered.write_bytes(b'x' * 2048)
        return rendered

    doctor(healthy=True, preview=preview)
    assert cli.cmd_doctor(argparse.Namespace()) == 0
    out = capsys.readouterr().out
    assert f'pyvista    {sys.executable}' in out
    assert 'interactive scene, 2 KB' in out
    assert 'all checks passed' in out
    assert not rendered.exists()


@pytest.mark.parametrize(
    ('listing', 'claims', 'problems', 'line'),
    [
        (f'-    {cli.NATIVE_VIEWER}(1.0)', True, 0, '✓ native     .ply, .stl, .obj, USD and .abc'),
        (f'+    {cli.NATIVE_VIEWER}(1.0)', True, 1, '✗ native     macOS still previews'),
        (
            f'-    {cli.NATIVE_VIEWER}(1.0)',
            False,
            1,
            '✗ native     the installed extension predates',
        ),
    ],
)
def test_doctor_checks_the_native_viewer_when_the_setting_is_on(
    capsys, doctor, home, monkeypatch, tmp_path, listing, claims, problems, line
):
    """With native formats on, the doctor needs the viewer down and an extension that claims."""
    rendered = tmp_path / 'smoke.ply'

    def preview(sample, timeout):
        rendered.write_bytes(b'x')
        return rendered

    doctor(healthy=True, preview=preview)
    config.put('native_formats', True)
    app = home / 'Applications' / plist.APP_BUNDLE
    info = plist.plugins_folder(app) / f'{plist.EXT_EXECUTABLE}.appex' / 'Contents' / 'Info.plist'
    written = plist.extension_plist(['.vtu'])
    if not claims:
        written['NSExtension']['NSExtensionAttributes']['QLSupportedContentTypes'] = ['x']
    plist.write(written, info)
    run = cli._run
    monkeypatch.setattr(
        cli,
        '_run',
        lambda command: listing if command[:2] == ['/usr/bin/pluginkit', '-m'] else run(command),
    )
    assert cli.cmd_doctor(argparse.Namespace()) == problems
    assert line in capsys.readouterr().out


def test_doctor_notes_a_pyvista_command_that_does_not_run(capsys, doctor, tmp_path):
    """A pyvista command that fails to run only rules out still images."""
    rendered = tmp_path / 'smoke.ply'

    def preview(sample, timeout):
        rendered.write_bytes(b'x')
        return rendered

    doctor(healthy=True, preview=preview, pyvista_runs=False)
    assert cli.cmd_doctor(argparse.Namespace()) == 0
    assert 'still images unavailable' in capsys.readouterr().out


def test_doctor_reports_a_failed_render(capsys, doctor, tmp_path):
    """A render that fails through the service is a problem, with the reason shown."""

    def preview(sample, timeout):
        message = 'service did not answer'
        raise RenderError(message)

    doctor(healthy=True, preview=preview)
    assert cli.cmd_doctor(argparse.Namespace()) == 1
    out = capsys.readouterr().out
    assert 'service did not answer' in out
    assert '1 problem(s) found' in out


def test_doctor_says_when_the_service_is_loaded_but_not_running(capsys, doctor):
    """A service launchd has but cannot start, as after a stale upgrade, is a problem."""

    def preview(sample, timeout):
        message = 'service did not answer'
        raise RenderError(message)

    doctor(healthy=True, preview=preview, state='spawn scheduled')
    assert cli.cmd_doctor(argparse.Namespace()) == 1
    assert '✗ service    loaded but spawn scheduled; see' in capsys.readouterr().out


def test_doctor_counts_every_missing_piece(capsys, doctor):
    """Without an environment, app, extensions or service, each is reported."""
    doctor(healthy=False, preview=lambda *a, **k: pytest.fail('rendered'))
    assert cli.cmd_doctor(argparse.Namespace()) == 1
    out = capsys.readouterr().out
    assert '(using defaults)' in out
    assert 'still images unavailable' in out
    assert 'thumbnails not registered' in out
    assert '5 problem(s) found' in out


def test_uninstall_asks_first_on_a_terminal(capsys, commands, home, monkeypatch):
    """An interactive uninstall stops unless the answer is yes."""
    config.CONFIG_PATH.parent.mkdir(parents=True)
    config.CONFIG_PATH.write_text('{}')
    monkeypatch.setattr(cli.sys.stdin, 'isatty', lambda: True)
    monkeypatch.setattr('builtins.input', lambda prompt: 'n')
    assert cli.cmd_uninstall(argparse.Namespace(all=False, yes=False)) == 1
    assert 'pvql uninstall --all' in capsys.readouterr().out
    assert commands == []


def test_uninstall_without_uv_says_how_to_remove_the_command(capsys, commands, home, monkeypatch):
    """Without uv the pvql command is left, with the command that removes it."""
    monkeypatch.setattr(cli.sys.stdin, 'isatty', lambda: True)
    monkeypatch.setattr('builtins.input', lambda prompt: 'y')
    monkeypatch.setattr(cli.shutil, 'which', lambda name: str(home / 'no-uv'))
    assert cli.cmd_uninstall(argparse.Namespace(all=False, yes=False)) == 0
    assert 'uv tool uninstall pyvista-quicklook' in capsys.readouterr().out
    assert not any('tool' in command for command in commands)


@pytest.mark.parametrize(
    ('elapsed', 'seconds'),
    [('00:07', 7), ('05:22', 322), ('03:04:05', 11045), ('2-03:04:05', 183845)],
)
def test_finder_started_reads_the_elapsed_time_ps_reports(monkeypatch, elapsed, seconds):
    """Every form of ps's elapsed time gives the moment the Finder started."""
    answers = {'/usr/bin/pgrep': '1784\n', '/bin/ps': f'  {elapsed}\n'}
    monkeypatch.setattr(cli, '_run', lambda command: answers[command[0]].strip())
    assert cli.finder_started() == pytest.approx(time.time() - seconds, abs=1)


def test_finder_started_asks_about_this_users_finder(monkeypatch):
    """Only the Finder of the user running the helper counts, and the first one listed."""
    ran = []

    def run(command):
        ran.append(command)
        return '1784\n2001' if command[0] == '/usr/bin/pgrep' else '00:07'

    monkeypatch.setattr(cli, '_run', run)
    assert cli.finder_started() is not None
    assert ran[0][1:] == ['-x', '-U', str(os.getuid()), 'Finder']
    assert ran[1][-1] == '1784'


@pytest.mark.parametrize('pids', ['', 'garbled'])
def test_finder_started_is_none_without_a_finder(monkeypatch, pids):
    """No running Finder, or output that cannot be read, gives no start time."""
    monkeypatch.setattr(cli, '_run', lambda command: pids)
    assert cli.finder_started() is None


@pytest.fixture
def retired(home):
    """Return a setter for when the old extension's exchange directory was retired."""
    data = cli.daemon_mod.legacy_drop_dir().parent

    def setup(at):
        data.mkdir(parents=True, exist_ok=True)
        os.utime(data, (at, at))

    return setup


@pytest.mark.parametrize(
    ('started', 'stale'),
    [(1000.0, True), (1497.0, True), (1498.0, False), (2000.0, False), (None, False)],
)
def test_a_finder_from_before_the_retirement_is_stale(monkeypatch, retired, started, stale):
    """Only a Finder started well before the old extension was retired is stale."""
    retired(at=1500)
    monkeypatch.setattr(cli, 'finder_started', lambda: started)
    assert cli.finder_is_stale() is stale


def test_without_an_old_extension_the_finder_is_not_looked_up(monkeypatch, home):
    """A Mac that never ran the old extension has no stale Finder, and no Finder is queried."""
    monkeypatch.setattr(cli, 'finder_started', lambda: pytest.fail('looked up the Finder'))
    assert cli.finder_is_stale() is False


def test_retiring_the_old_extension_makes_an_older_finder_stale(commands, monkeypatch, home):
    """Retiring the old service is what dates the retirement the Finder is compared against."""
    old = cli.daemon_mod.legacy_drop_dir()
    old.mkdir(parents=True)
    os.utime(old.parent, (1000, 1000))
    monkeypatch.setattr(cli, 'finder_started', lambda: time.time() - 60)
    assert cli.finder_is_stale() is False
    cli.retire_legacy_service()
    assert cli.finder_is_stale() is True


@pytest.mark.parametrize(('stale', 'said'), [(True, 'stale'), (False, 'current')])
def test_finder_command_says_whether_to_restart(capsys, monkeypatch, stale, said):
    """The finder command prints one word the installer acts on."""
    monkeypatch.setattr(cli, 'finder_is_stale', lambda: stale)
    assert cli.cmd_finder(argparse.Namespace()) == 0
    assert capsys.readouterr().out == f'{said}\n'


def test_doctor_reports_a_stale_finder(capsys, doctor, monkeypatch, tmp_path):
    """A Finder that still asks for the old extension is a problem, with the remedy."""
    rendered = tmp_path / 'smoke.ply'

    def preview(sample, timeout):
        rendered.write_bytes(b'x')
        return rendered

    doctor(healthy=True, preview=preview)
    monkeypatch.setattr(cli, 'finder_is_stale', lambda: True)
    assert cli.cmd_doctor(argparse.Namespace()) == 1
    out = capsys.readouterr().out
    assert 'run "killall Finder"' in out
    assert '1 problem(s) found' in out


def test_mirror_copies_nothing_until_settings_exist(home):
    """Without a settings file there is nothing to put where the extension reads."""
    config.mirror()
    assert not config.MIRROR_PATH.exists()
    config.save({'timeout': 5})
    assert json.loads(config.MIRROR_PATH.read_text()) == {'timeout': 5}


def test_mirror_follows_a_hand_edited_or_removed_file(home):
    """Settings edited outside pvql reach the extension's copy, and a removed file removes it."""
    config.save({'timeout': 5})
    config.CONFIG_PATH.write_text(json.dumps({'force_interactive': True}))
    config.mirror()
    assert json.loads(config.MIRROR_PATH.read_text()) == {'force_interactive': True}
    config.CONFIG_PATH.unlink()
    config.mirror()
    assert not config.MIRROR_PATH.exists()


@pytest.fixture
def installation(home, commands, monkeypatch):
    """Lay out a fake installation under tmp_path and record the commands run."""
    support = home / 'support'
    for folder in ('venv', 'src', 'unpacked'):
        (support / folder).mkdir(parents=True)
        (support / folder / 'file').write_text('x')
    (support / 'config.json').write_text('{}')
    app = home / 'Applications' / plist.APP_BUNDLE
    plist.plugins_folder(app).mkdir(parents=True)
    (home / 'cache').mkdir()
    (home / 'agent.plist').write_text('x')
    (home / 'container').mkdir()
    (home / 'pvqld.log').write_text('x')
    (home / 'uv').write_text('#!/bin/sh\n')
    monkeypatch.setattr(cli.shutil, 'which', lambda name: str(home / 'uv'))
    return SimpleNamespace(home=home, commands=commands)


def test_uninstall_removes_everything_but_the_config(installation, capsys):
    """`pvql uninstall --yes` removes the installation and keeps the config file."""
    home = installation.home
    assert cli.cmd_uninstall(argparse.Namespace(yes=True, all=False)) == 0
    for gone in (
        f'Applications/{plist.APP_BUNDLE}',
        'agent.plist',
        'cache',
        'container',
        'pvqld.log',
        'support/venv',
        'support/src',
        'support/unpacked',
    ):
        assert not (home / gone).exists(), gone
    assert (home / 'support' / 'config.json').exists()
    joined = [' '.join(str(part) for part in command) for command in installation.commands]
    removed = [c for c in joined if 'pluginkit -r' in c]
    assert any(
        str(plist.plugins_folder(home / 'Applications' / plist.APP_BUNDLE)) in c for c in removed
    )
    assert any(
        'Contents/PlugIns/PyVistaQuickLookExtension.appex' in c and 'Contents/Library' not in c
        for c in removed
    )
    assert any(plist.LEGACY_HOLDER_BUNDLE in c for c in removed)
    unregistered = [c for c in joined if 'lsregister -u' in c]
    assert any(
        c.endswith(str(plist.plugins_folder(home / 'Applications' / plist.APP_BUNDLE).parents[1]))
        for c in unregistered
    )
    assert any(c.endswith(f'Applications/{plist.APP_BUNDLE}') for c in unregistered)
    assert any('launchctl bootout' in c for c in joined)
    assert any(c.endswith('uv tool uninstall pyvista-quicklook') for c in joined)
    assert 'keeps' in capsys.readouterr().out


def test_uninstall_removes_a_bundle_under_the_old_name(installation):
    """A bundle installed before the rename is found and removed as well."""
    legacy = installation.home / 'Applications' / plist.LEGACY_APP_BUNDLE
    (legacy / 'Contents' / 'PlugIns').mkdir(parents=True)
    assert legacy in cli.installed_apps()
    assert cli.cmd_uninstall(argparse.Namespace(yes=True, all=False)) == 0
    assert not legacy.exists()


def test_uninstall_removes_the_service_under_the_old_label(installation):
    """A service installed under the io.github.user27182 label is stopped and removed."""
    home = installation.home
    (home / 'old-agent.plist').write_text('x')
    cli.daemon_mod.legacy_drop_dir().mkdir(parents=True)
    assert cli.cmd_uninstall(argparse.Namespace(yes=True, all=False)) == 0
    assert not (home / 'old-agent.plist').exists()
    assert not cli.daemon_mod.legacy_drop_dir().exists()
    joined = [' '.join(str(part) for part in command) for command in installation.commands]
    assert any('bootout' in c and c.endswith(f'/{daemon.LEGACY_LABEL}') for c in joined)


def test_uninstall_all_removes_the_config_too(installation):
    """`pvql uninstall --all` removes the config file as well."""
    assert cli.cmd_uninstall(argparse.Namespace(yes=True, all=True)) == 0
    assert not (installation.home / 'support' / 'config.json').exists()


def test_uninstall_only_reports_without_a_terminal(installation, capsys):
    """Without --yes and without a terminal to ask on, nothing is removed."""
    assert cli.cmd_uninstall(argparse.Namespace(yes=False, all=False)) == 1
    assert (installation.home / 'Applications' / plist.APP_BUNDLE).exists()
    assert installation.commands == []
    out = capsys.readouterr().out
    assert 'pvql uninstall --yes' in out
    # The config file is kept, so the flag that takes it too is named separately.
    assert '--all' in out


def test_uninstall_without_a_config_file_does_not_mention_keeping_it(installation, capsys):
    """With no config file there is nothing to keep, so --all is not suggested either."""
    (installation.home / 'support' / 'config.json').unlink()
    assert cli.cmd_uninstall(argparse.Namespace(yes=False, all=False)) == 1
    out = capsys.readouterr().out
    assert 'pvql uninstall --yes' in out
    assert 'keeps' not in out
    assert '--all' not in out


def test_uv_note_explains_how_to_remove_an_installed_uv(tmp_path, monkeypatch):
    """Uv the installer fetched is explained; uv from elsewhere is left to its owner."""
    monkeypatch.setattr(cli.Path, 'home', classmethod(lambda cls: tmp_path))
    ours = cli.uv_note(tmp_path / '.local' / 'bin' / 'uv')
    assert 'uv cache clean' in ours
    assert 'not only this one' in ours
    assert 'whatever installed it' in cli.uv_note(Path('/opt/homebrew/bin/uv'))


def test_service_install_retires_the_agent_of_the_old_label(commands, home):
    """Installing stops and removes the service an install under the old label left."""
    (home / 'old-agent.plist').write_text('x')
    cli.daemon_mod.legacy_drop_dir().mkdir(parents=True)
    args = argparse.Namespace(install=True, uninstall=False, helper='/opt/pvql')
    assert cli.cmd_service(args) == 0
    assert not (home / 'old-agent.plist').exists()
    assert not cli.daemon_mod.legacy_drop_dir().exists()
    joined = [' '.join(str(part) for part in command) for command in commands]
    assert any('bootout' in c and c.endswith(f'/{daemon.LEGACY_LABEL}') for c in joined)


def test_services_are_named_in_this_users_session(commands):
    """A service is addressed in the gui domain of the user running the helper."""
    assert cli.service_target() == f'gui/{os.getuid()}/{daemon.LABEL}'
    assert cli.service_target('other').endswith('/other')
    cli.unload_service('other')
    assert commands == [['/bin/launchctl', 'bootout', f'gui/{os.getuid()}/other']]


def test_parser_accepts_every_subcommand():
    """Each documented subcommand parses."""
    parser = cli.build_parser()
    for argv in (
        ['preview', 'mesh.vtu'],
        ['prebuild', 'dir'],
        ['types', '--all'],
        ['plist', '--app', 'a.plist', '--extension', 'e.plist'],
        ['config', '--init'],
        ['cache', '--clear'],
        ['finder'],
        ['doctor'],
        ['daemon'],
        ['service', '--install'],
        ['uninstall', '--yes', '--all'],
    ):
        assert parser.parse_args(argv).func is not None


def test_cache_prune_and_clear_cannot_be_combined(capsys):
    """Asking to prune and to clear at once is refused rather than half done."""
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(['cache', '--prune', '--clear'])
    assert 'not allowed with' in capsys.readouterr().err


def test_claimed_extensions_follows_config():
    """The claimed set reflects the config additions and removals."""
    claimed = cli.claimed_extensions({'extensions': {'add': ['.msh'], 'remove': ['.vtu']}})
    assert '.msh' in claimed
    assert '.vtu' not in claimed


def test_types_lists_claimed_extensions(capsys, monkeypatch):
    """`pvql types` prints one line per claimed extension, and marks macOS's own apart."""
    monkeypatch.setattr(cli.config_mod, 'load', lambda: dict(config.DEFAULTS))
    assert cli.cmd_types(argparse.Namespace(all=False)) == 0
    lines = capsys.readouterr().out.splitlines()
    assert any(line.startswith('✓ .vtu ') for line in lines)
    [stl] = [line for line in lines if ' .stl ' in f' {line}']
    assert stl.startswith('→ .stl ')
    assert 'not previewed' in stl
    assert any('extensions claimed' in line for line in lines)


def test_types_lists_an_added_extension_it_does_not_know(capsys, monkeypatch):
    """An extension added through the config is listed even when the table lacks it."""
    added = {**config.DEFAULTS, 'extensions': {'add': ['.xyz'], 'remove': []}}
    monkeypatch.setattr(cli.config_mod, 'load', lambda: added)
    assert cli.cmd_types(argparse.Namespace(all=False)) == 0
    assert '.xyz' in capsys.readouterr().out


def test_plist_writes_every_file(tmp_path, monkeypatch):
    """`pvql plist` writes the app's and both extensions' property lists."""
    monkeypatch.setattr(cli.config_mod, 'load', lambda: dict(config.DEFAULTS))
    app = tmp_path / 'App.plist'
    ext = tmp_path / 'Ext.plist'
    thumb = tmp_path / 'Thumb.plist'
    holder = tmp_path / 'Holder.plist'
    args = argparse.Namespace(
        app=str(app),
        extension=str(ext),
        thumbnail=str(thumb),
        holder=str(holder),
        helper='/bin/pvql',
    )
    assert cli.cmd_plist(args) == 0
    held = plistlib.loads(holder.read_bytes())
    assert held['CFBundleIdentifier'] == plist.HOLDER_BUNDLE_ID
    assert held['LSBackgroundOnly'] is True
    assert held['PVQLHelperPath'] == '/bin/pvql'
    assert plistlib.loads(app.read_bytes())['PVQLHelperPath'] == '/bin/pvql'
    assert plistlib.loads(ext.read_bytes())['CFBundlePackageType'] == 'XPC!'
    assert plistlib.loads(thumb.read_bytes())['CFBundleIdentifier'] == plist.THUMB_BUNDLE_ID


def test_preview_reports_failures_on_stderr(capsys, monkeypatch):
    """A render failure exits non-zero and explains itself."""

    def fail(path, config):
        message = 'nope'
        raise RenderError(message)

    monkeypatch.setattr(cli.config_mod, 'load', lambda: dict(config.DEFAULTS))
    monkeypatch.setattr(cli.daemon_mod, 'produce', fail)
    args = argparse.Namespace(path='mesh.vtu', output=None, no_cache=True)
    assert cli.cmd_preview(args) == 1
    assert 'nope' in capsys.readouterr().err


def test_preview_prints_the_cached_path(capsys, monkeypatch, tmp_path):
    """A successful build prints where the scene landed."""
    ply = tmp_path / 'out.ply'
    ply.write_bytes(b'x')
    monkeypatch.setattr(cli.config_mod, 'load', lambda: dict(config.DEFAULTS))
    monkeypatch.setattr(cli.daemon_mod, 'produce', lambda path, config: ply)
    args = argparse.Namespace(path='mesh.vtu', output=None, no_cache=False)
    assert cli.cmd_preview(args) == 0
    assert str(ply) in capsys.readouterr().out


def test_prebuild_builds_every_claimed_file_in_a_directory(capsys, monkeypatch, tmp_path):
    """`pvql prebuild DIR` builds the claimed files it finds and reports the failures."""
    (tmp_path / 'good.vtu').write_text('x')
    (tmp_path / 'bad.vtp').write_text('x')
    (tmp_path / 'notes.txt').write_text('x')
    built = []

    def produce(target, config):
        if target.suffix == '.vtp':
            message = 'unreadable'
            raise RenderError(message)
        built.append(target.name)
        return target

    monkeypatch.setattr(cli.config_mod, 'load', lambda: dict(config.DEFAULTS))
    monkeypatch.setattr(cli.daemon_mod, 'produce', produce)
    assert cli.cmd_prebuild(argparse.Namespace(paths=[str(tmp_path)])) == 1
    assert built == ['good.vtu']
    captured = capsys.readouterr()
    assert '1 of 2 cached' in captured.out
    assert 'bad.vtp: unreadable' in captured.err


def test_cache_reports_its_size(capsys, monkeypatch, tmp_path):
    """`pvql cache` counts previews, not the descriptions kept beside them."""
    (tmp_path / 'a.png').write_bytes(b'0123456789')
    (tmp_path / 'v-a.json').write_bytes(b'{}')
    monkeypatch.setattr(cli.config_mod, 'CACHE_DIR', tmp_path)
    assert cli.cmd_cache(argparse.Namespace(clear=False, prune=False)) == 0
    assert '1 previews' in capsys.readouterr().out


def test_cache_clear_removes_previews_but_not_the_stamp(capsys, monkeypatch, tmp_path):
    """`pvql cache --clear` deletes the previews and leaves the preload stamp."""
    (tmp_path / 'a.png').write_bytes(b'x')
    (tmp_path / 'b.ply').write_bytes(b'x')
    (tmp_path / 'b.json').write_bytes(b'{}')
    (tmp_path / '.preloaded').touch()
    monkeypatch.setattr(cli.config_mod, 'CACHE_DIR', tmp_path)
    assert cli.cmd_cache(argparse.Namespace(clear=True, prune=False)) == 0
    assert 'removed 2 cached previews' in capsys.readouterr().out
    assert [p.name for p in tmp_path.iterdir()] == ['.preloaded']


def test_cache_prune_reports_what_went(capsys, monkeypatch, tmp_path):
    """`pvql cache --prune` drops stale previews now and reports the count."""
    monkeypatch.setattr(cli.config_mod, 'CACHE_DIR', tmp_path)
    monkeypatch.setattr(cli.cache_mod, 'prune', lambda config: 3)
    assert cli.cmd_cache(argparse.Namespace(clear=False, prune=True)) == 0
    assert 'removed 3 cached previews and descriptions' in capsys.readouterr().out


def test_cache_prune_also_discards_scenes_of_other_versions(capsys, monkeypatch, tmp_path):
    """`pvql cache --prune` takes an older exporter's scenes along with the stale previews."""
    monkeypatch.setattr(cli.config_mod, 'CACHE_DIR', tmp_path)
    monkeypatch.setattr(cli.cache_mod, 'prune', lambda config: 1)
    (tmp_path / 'old-1.ply').write_bytes(b'x')
    (tmp_path / 'old-1.json').write_bytes(b'{}')
    assert cli.cmd_cache(argparse.Namespace(clear=False, prune=True)) == 0
    assert 'removed 3 cached previews and descriptions' in capsys.readouterr().out
    assert not list(tmp_path.iterdir())


def test_cache_size_survives_a_file_that_vanishes(capsys, monkeypatch, tmp_path):
    """`pvql cache` adds up what is there, even as the service prunes beside it."""
    monkeypatch.setattr(cli.config_mod, 'CACHE_DIR', tmp_path)
    (tmp_path / 'a.png').write_bytes(b'0123456789')
    monkeypatch.setattr(
        cli.cache_mod, 'files', lambda *patterns: [tmp_path / 'a.png', tmp_path / 'gone.png']
    )
    assert cli.cmd_cache(argparse.Namespace(clear=False, prune=False)) == 0
    assert '0.0 MB' in capsys.readouterr().out


def test_config_init_with_an_interpreter_clears_a_stale_cli(tmp_path, monkeypatch):
    """An environment named by interpreter does not keep an unrelated CLI on record."""
    path = tmp_path / 'config.json'
    monkeypatch.setattr(config, 'CONFIG_PATH', path)
    monkeypatch.setattr(config, 'APP_SUPPORT', tmp_path)
    path.write_text(json.dumps({'pyvista': '/old/bin/pyvista'}))
    args = argparse.Namespace(
        init=True, python='/new/bin/python', pyvista=None, helper='/bin/pvql'
    )
    cli.cmd_config(args)
    stored = json.loads(path.read_text())
    assert stored['python'] == '/new/bin/python'
    assert 'pyvista' not in stored


def test_doctor_gives_the_service_as_long_as_the_extension_would(capsys, doctor, tmp_path):
    """The smoke render waits as long as a reply may take, so a cold service still passes."""
    rendered = tmp_path / 'smoke.ply'
    waited = []

    def preview(sample, timeout):
        waited.append(timeout)
        rendered.write_bytes(b'x')
        return rendered

    doctor(healthy=True, preview=preview)
    assert cli.cmd_doctor(argparse.Namespace()) == 0
    assert waited == [daemon.reply_seconds(dict(config.DEFAULTS))]


@pytest.mark.parametrize('helper', ['', None])
def test_plist_leaves_the_helper_out_when_asked(tmp_path, monkeypatch, helper):
    """An empty --helper names no helper; none at all names the pvql on PATH, if any."""
    monkeypatch.setattr(cli.config_mod, 'load', lambda: dict(config.DEFAULTS))
    monkeypatch.setattr(cli.shutil, 'which', lambda name: '/found/pvql')
    app = tmp_path / 'App.plist'
    args = argparse.Namespace(
        app=str(app), extension=None, thumbnail=None, holder=None, helper=helper
    )
    assert cli.cmd_plist(args) == 0
    stored = plistlib.loads(app.read_bytes())
    if helper is None:
        assert stored['PVQLHelperPath'] == '/found/pvql'
    else:
        assert 'PVQLHelperPath' not in stored
