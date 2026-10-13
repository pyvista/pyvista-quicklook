"""Tests for the ``pvql`` subcommands, with every system command replaced."""

from __future__ import annotations

import argparse
import json
import os
import plistlib
import runpy
import subprocess
import sys
import time

import pytest

from pyvista_quicklook import cli
from pyvista_quicklook import config
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


def test_service_uninstall_removes_both_agents(capsys, commands, home):
    """Uninstalling stops the service and deletes its agent, old label included."""
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


def test_service_install_falls_back_to_the_given_helper(commands, home):
    """Without a pyvista-quicklook beside it, the agent runs the helper it was given."""
    helper = home / 'bin' / 'pvql'
    args = argparse.Namespace(install=True, uninstall=False, helper=str(helper))
    assert cli.cmd_service(args) == 0
    agent = plistlib.loads((home / 'agent.plist').read_bytes())
    assert agent['ProgramArguments'] == [str(helper), 'daemon']
    assert any('kickstart' in command for command in commands)


def test_service_install_retries_then_reports_launchd(capsys, monkeypatch, home):
    """A bootstrap launchd keeps refusing is retried, then reported with its message."""
    bootstraps = []

    def run(command, **kwargs):
        if 'bootstrap' in command:
            bootstraps.append(command)
            return subprocess.CompletedProcess(command, 5, '', 'Input/output error\n')
        return subprocess.CompletedProcess(command, 0, '', '')

    monkeypatch.setattr(cli.subprocess, 'run', run)
    monkeypatch.setattr(cli.time, 'sleep', lambda seconds: None)
    args = argparse.Namespace(install=True, uninstall=False, helper='/bin/pvql')
    assert cli.cmd_service(args) == 1
    assert len(bootstraps) == 5
    assert 'could not load' in capsys.readouterr().err


def test_service_reports_a_job_that_is_not_loaded(capsys, monkeypatch):
    """With no flags, the status of the job is printed, or that it is not loaded."""
    monkeypatch.setattr(cli, '_run', lambda command: '')
    assert cli.cmd_service(argparse.Namespace(install=False, uninstall=False)) == 0
    assert 'is not loaded' in capsys.readouterr().out


def test_warmup_reports_the_time_taken(capsys, monkeypatch):
    """A successful warm-up says how long it took."""
    monkeypatch.setattr(cli.warmup_mod, 'warm', lambda: 1.25)
    assert cli.cmd_warmup(argparse.Namespace()) == 0
    assert '1.2 s' in capsys.readouterr().out


def test_warmup_reports_a_failure(capsys, monkeypatch):
    """A failed warm-up is reported on stderr with a failing status."""

    def fail():
        message = 'no interpreter'
        raise RenderError(message)

    monkeypatch.setattr(cli.warmup_mod, 'warm', fail)
    assert cli.cmd_warmup(argparse.Namespace()) == 1
    assert 'no interpreter' in capsys.readouterr().err


def test_warm_counts_failures_and_named_files(capsys, monkeypatch, tmp_path):
    """Named files are warmed as given, and each failure is reported in one line."""
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
    assert cli.cmd_warm(args) == 1
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
    """Without ``--app``, ``--extension`` or ``--thumbnail`` no file is written."""
    monkeypatch.setattr(cli.config_mod, 'load', lambda: dict(config.DEFAULTS))
    args = argparse.Namespace(app=None, extension=None, thumbnail=None, helper='/bin/pvql')
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

    def setup(*, healthy, preview=None, pyvista_runs=None):
        interpreter = sys.executable if healthy else None
        runs = healthy if pyvista_runs is None else pyvista_runs
        monkeypatch.setattr(cli.config_mod, 'find_python', lambda cfg: interpreter)
        monkeypatch.setattr(cli.config_mod, 'resolve_pyvista', lambda cfg: interpreter)
        monkeypatch.setattr(cli, '_succeeds', lambda command: runs)
        monkeypatch.setattr(cli, '_run', lambda command: 'loaded' if healthy else '')
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
