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
    (home / 'old-container').mkdir()
    args = argparse.Namespace(install=False, uninstall=True, helper=None)
    assert cli.cmd_service(args) == 0
    assert not (home / 'agent.plist').exists()
    assert not (home / 'old-agent.plist').exists()
    assert not (home / 'old-container').exists()
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
    """Without ``--app`` or ``--extension`` no file is written."""
    monkeypatch.setattr(cli.config_mod, 'load', lambda: dict(config.DEFAULTS))
    args = argparse.Namespace(app=None, extension=None, helper='/bin/pvql')
    assert cli.cmd_plist(args) == 0
    assert capsys.readouterr().out == ''


def test_config_shows_the_stored_file(capsys, home):
    """The configuration file is printed when it exists, and a hint when it does not."""
    args = argparse.Namespace(init=False)
    cli.cmd_config(args)
    assert 'pvql config --init' in capsys.readouterr().out
    config.CONFIG_PATH.parent.mkdir(parents=True)
    config.CONFIG_PATH.write_text(json.dumps({'timeout': 5}))
    cli.cmd_config(args)
    assert '"timeout": 5' in capsys.readouterr().out


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
    rendered.write_bytes(b'x')
    doctor(healthy=True, preview=lambda sample, timeout: rendered, pyvista_runs=False)
    assert cli.cmd_doctor(argparse.Namespace()) == 0
    assert 'still images unavailable' in capsys.readouterr().out


def test_doctor_reports_a_failed_render(capsys, doctor):
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
    """Without an environment, app, extension or service, each is reported."""
    doctor(healthy=False, preview=lambda *a, **k: pytest.fail('rendered'))
    assert cli.cmd_doctor(argparse.Namespace()) == 1
    out = capsys.readouterr().out
    assert '(using defaults)' in out
    assert 'still images unavailable' in out
    assert '4 problem(s) found' in out


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


@pytest.mark.parametrize('pids', ['', 'garbled'])
def test_finder_started_is_none_without_a_finder(monkeypatch, pids):
    """No running Finder, or output that cannot be read, gives no start time."""
    monkeypatch.setattr(cli, '_run', lambda command: pids)
    assert cli.finder_started() is None


@pytest.fixture
def retired(tmp_path, monkeypatch):
    """Retire the old extension's exchange directory at a set time; return a setter."""
    data = tmp_path / 'old-container' / 'Data'
    monkeypatch.setattr(cli.daemon_mod, 'legacy_drop_dir', lambda: data / 'tmp')

    def setup(at):
        data.mkdir(parents=True, exist_ok=True)
        os.utime(data, (at, at))

    return setup


@pytest.mark.parametrize(('started', 'stale'), [(1000.0, True), (2000.0, False), (None, False)])
def test_a_finder_from_before_the_retirement_is_stale(monkeypatch, retired, started, stale):
    """Only a Finder started before the old extension was retired is stale."""
    retired(at=1500)
    monkeypatch.setattr(cli, 'finder_started', lambda: started)
    assert cli.finder_is_stale() is stale


def test_without_an_old_extension_the_finder_is_current(monkeypatch, retired):
    """A Mac that never ran the old extension has no stale Finder."""
    monkeypatch.setattr(cli, 'finder_started', lambda: 1000.0)
    assert cli.finder_is_stale() is False


@pytest.mark.parametrize(('stale', 'said'), [(True, 'stale'), (False, 'current')])
def test_finder_command_says_whether_to_restart(capsys, monkeypatch, stale, said):
    """The finder command prints one word the installer acts on."""
    monkeypatch.setattr(cli, 'finder_is_stale', lambda: stale)
    assert cli.cmd_finder(argparse.Namespace()) == 0
    assert capsys.readouterr().out == f'{said}\n'


def test_doctor_reports_a_stale_finder(capsys, doctor, monkeypatch, tmp_path):
    """A Finder that still asks for the old extension is a problem, with the remedy."""
    rendered = tmp_path / 'smoke.ply'
    rendered.write_bytes(b'x')
    doctor(healthy=True, preview=lambda sample, timeout: rendered)
    monkeypatch.setattr(cli, 'finder_is_stale', lambda: True)
    assert cli.cmd_doctor(argparse.Namespace()) == 1
    out = capsys.readouterr().out
    assert 'run "killall Finder"' in out
    assert '1 problem(s) found' in out
