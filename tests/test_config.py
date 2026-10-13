"""Tests for reading, storing, and mirroring the settings."""

from __future__ import annotations

import json
import os

import pytest

from pyvista_quicklook import config


def test_config_load_merges_over_defaults(tmp_path, monkeypatch):
    """Stored overrides are read back, unknown keys dropped, and the rest follows the defaults."""
    path = tmp_path / 'config.json'
    stored = config.overrides({**config.DEFAULTS, 'timeout': 5})
    path.write_text(json.dumps({**stored, 'bogus': 1}))
    monkeypatch.setattr(config, 'CONFIG_PATH', path)
    loaded = config.load()
    assert loaded['timeout'] == 5
    assert 'bogus' not in loaded
    assert loaded['window_size'] == config.DEFAULTS['window_size']
    assert loaded['max_scene_points'] == config.DEFAULTS['max_scene_points']


def test_config_load_survives_broken_json(tmp_path, monkeypatch):
    """A corrupt config file falls back to defaults instead of raising."""
    path = tmp_path / 'config.json'
    path.write_text('{not json')
    monkeypatch.setattr(config, 'CONFIG_PATH', path)
    assert config.load()['timeout'] == config.DEFAULTS['timeout']


def test_config_load_ignores_a_file_that_is_not_an_object(tmp_path, monkeypatch):
    """A config file holding a JSON list, not settings, leaves the defaults in force."""
    path = tmp_path / 'config.json'
    path.write_text(json.dumps([{'timeout': 5}]))
    monkeypatch.setattr(config, 'CONFIG_PATH', path)
    loaded = config.load()
    assert loaded['timeout'] == config.DEFAULTS['timeout']
    assert loaded['extensions'] == config.DEFAULTS['extensions']


@pytest.mark.parametrize(
    ('key', 'value', 'expected'),
    [
        ('timeout', 'fast', 60),
        ('timeout', 2.5, 2.5),
        ('timeout', 0, 60),
        ('window_size', [1024], [1024, 1024]),
        ('window_size', [0, 10], [1024, 1024]),
        ('interactive', 'yes', True),
        ('info_panel', 'x', 'automatic'),
        ('max_scene_points', -5, 2000000),
        ('max_scene_points', True, 2000000),
        ('cache_check_minutes', float('inf'), 1440),
        ('extra_args', 'not a list', []),
        ('python', 7, None),
        ('python', None, None),
        ('extensions', 'not a dict', config.DEFAULTS['extensions']),
        ('extensions', {'add': ['.foo'], 'remove': 'x'}, {'add': ['.foo']}),
    ],
)
def test_a_hand_edited_value_of_the_wrong_kind_falls_back_to_the_default(key, value, expected):
    """A value a setting cannot take is replaced by its default; one it can is kept."""
    assert config.coerced(key, value) == expected


def test_config_load_coerces_what_it_reads(tmp_path, monkeypatch):
    """Hand-edited values of the wrong kind do not reach the service."""
    path = tmp_path / 'config.json'
    path.write_text(json.dumps({'timeout': 'fast', 'window_size': [1024], 'interactive': 'yes'}))
    monkeypatch.setattr(config, 'CONFIG_PATH', path)
    loaded = config.load()
    assert loaded['timeout'] == 60
    assert loaded['window_size'] == [1024, 1024]
    assert loaded['interactive'] is True


def test_overrides_drops_settings_that_match_the_defaults():
    """Only deliberate changes are stored, so defaults stay free to improve."""
    merged = {**config.DEFAULTS, 'python': '/bin/python', 'timeout': 5}
    stored = config.overrides(merged)
    assert stored == {'python': '/bin/python', 'timeout': 5}
    assert 'max_scene_points' not in stored


def test_mirror_leaves_an_identical_copy_untouched(home):
    """A mirror that already matches the file is not rewritten on every poll."""
    config.save({'timeout': 5})
    past = 1_000_000_000
    os.utime(config.MIRROR_PATH, (past, past))
    config.mirror()
    assert config.MIRROR_PATH.stat().st_mtime == past
    assert json.loads(config.MIRROR_PATH.read_text()) == {'timeout': 5}


def test_executable_rejects_what_cannot_run(tmp_path):
    """Missing, unset, and non-executable paths are all reported as not found."""
    assert config.executable(None) is None
    assert config.executable(str(tmp_path / 'absent')) is None
    plain = tmp_path / 'plain'
    plain.write_text('data')
    assert config.executable(str(plain)) is None


def test_executable_accepts_a_runnable_file(tmp_path):
    """An executable file is returned as given."""
    tool = tmp_path / 'pyvista'
    tool.write_text('#!/bin/sh\n')
    tool.chmod(0o755)
    assert config.executable(str(tool)) == str(tool)


def test_find_python_prefers_the_configured_interpreter(tmp_path):
    """An explicit interpreter is used as given."""
    tool = tmp_path / 'python'
    tool.write_text('#!/bin/sh\n')
    tool.chmod(0o755)
    assert config.find_python({'python': str(tool)}) == str(tool)


def test_find_python_reports_nothing_without_an_environment():
    """No interpreter and no CLI means no environment, whatever PATH holds."""
    assert config.find_python({}) is None
    assert config.resolve_pyvista({}) is None


def test_resolve_pyvista_stays_inside_the_configured_environment(tmp_path):
    """A configured interpreter without a sibling CLI does not fall back to PATH."""
    interpreter = tmp_path / 'python'
    interpreter.write_text('#!/bin/sh\n')
    interpreter.chmod(0o755)
    assert config.resolve_pyvista({'python': str(interpreter)}) is None

    sibling = tmp_path / 'pyvista'
    sibling.write_text('#!/bin/sh\n')
    sibling.chmod(0o755)
    assert config.resolve_pyvista({'python': str(interpreter)}) == str(sibling)


def test_prune_interval_is_read_as_whole_minutes():
    """The interval takes a whole number of minutes, where 0 turns pruning off."""
    assert config.parse('cache_check_minutes', ['15']) == 15
    assert config.parse('cache_check_minutes', ['0']) == 0
    for bad in ('-1', '1.5', 'often'):
        with pytest.raises(config.SettingError, match='whole number of minutes'):
            config.parse('cache_check_minutes', [bad])


def test_the_former_warm_on_start_key_is_still_read(monkeypatch, tmp_path):
    """A configuration written before the rename keeps its setting."""
    path = tmp_path / 'config.json'
    path.write_text('{"warm_on_start": false}')
    monkeypatch.setattr(config, 'CONFIG_PATH', path)
    assert config.load()['preload_on_start'] is False
