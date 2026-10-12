"""Configuration loading for the Quick Look helper."""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Any

APP_SUPPORT = Path.home() / 'Library' / 'Application Support' / 'PyVistaQuickLook'
CONFIG_PATH = APP_SUPPORT / 'config.json'
CACHE_DIR = Path.home() / 'Library' / 'Caches' / 'PyVistaQuickLook'
LOG_PATH = APP_SUPPORT / 'pvql.log'

DEFAULTS: dict[str, Any] = {
    'python': None,
    'pvql': None,
    'interactive': True,
    'info_panel': 'automatic',
    'warm_on_start': True,
    'max_scene_points': 2000000,
    'max_glyph_points': 20000,
    'window_size': [1024, 1024],
    'timeout': 60,
    'max_file_size_mb': 512,
    'background': None,
    'extra_args': [],
    'extensions': {'add': [], 'remove': []},
    'cache': True,
    'log': False,
}


def load() -> dict[str, Any]:
    """Return the merged configuration, falling back to defaults."""
    config = json.loads(json.dumps(DEFAULTS))
    if CONFIG_PATH.is_file():
        try:
            user = json.loads(CONFIG_PATH.read_text())
        except OSError, json.JSONDecodeError:
            user = {}
        if isinstance(user, dict):
            config.update({k: v for k, v in user.items() if k in DEFAULTS})
    config['extensions'] = {**DEFAULTS['extensions'], **(config.get('extensions') or {})}
    return config


def overrides(config: dict[str, Any]) -> dict[str, Any]:
    """Return only the settings that differ from the defaults.

    Storing the whole dictionary would freeze every default at install time, so a
    later improvement to one would never reach an existing installation.
    """
    return {key: value for key, value in config.items() if value != DEFAULTS.get(key)}


# How ``pvql config set`` reads a value for each key.
KINDS: dict[str, str] = {
    'python': 'executable',
    'pvql': 'executable',
    'interactive': 'switch',
    'info_panel': 'choice',
    'warm_on_start': 'switch',
    'max_scene_points': 'count',
    'max_glyph_points': 'count',
    'window_size': 'pixels',
    'timeout': 'seconds',
    'max_file_size_mb': 'count',
    'background': 'text',
    'extra_args': 'words',
    'extensions.add': 'extensions',
    'extensions.remove': 'extensions',
    'cache': 'switch',
    'log': 'switch',
}
CHOICES: dict[str, tuple[str, ...]] = {
    'info_panel': ('automatic', 'side', 'below', 'compact', 'hidden'),
}
SWITCHES = {
    'true': True,
    'yes': True,
    'on': True,
    '1': True,
    'false': False,
    'no': False,
    'off': False,
    '0': False,
}


class SettingError(ValueError):
    """A key or value that ``pvql config`` cannot store, with what would be accepted."""


def stored() -> dict[str, Any]:
    """Return what the configuration file holds, refusing a file that is not a JSON object."""
    if not CONFIG_PATH.is_file():
        return {}
    try:
        user = json.loads(CONFIG_PATH.read_text())
    except json.JSONDecodeError as error:
        message = f'{CONFIG_PATH} is not valid JSON ({error}); fix or delete it first.'
        raise SettingError(message) from error
    if not isinstance(user, dict):
        message = f'{CONFIG_PATH} does not hold a JSON object; fix or delete it first.'
        raise SettingError(message)
    return user


def check_key(key: str) -> str:
    """Return a settable key, or say which keys there are."""
    if key not in KINDS:
        message = f'Unknown setting "{key}". Settings: {", ".join(KINDS)}.'
        raise SettingError(message)
    return key


def parse(key: str, values: list[str]) -> Any:
    """Return the value a setting takes from command-line words, or explain what it needs."""
    kind = KINDS[check_key(key)]
    if kind in ('words', 'extensions'):
        if kind == 'extensions':
            from .formats import normalize

            return [normalize(value) for value in values]
        return list(values)
    if kind == 'pixels':
        try:
            width, height = (int(v) for v in values)
        except ValueError:
            width = height = 0
        if width <= 0 or height <= 0:
            message = f'{key} takes a width and a height in pixels, such as: 1024 768'
            raise SettingError(message)
        return [width, height]
    if len(values) != 1:
        message = f'{key} takes one value.'
        raise SettingError(message)
    value = values[0]
    if kind == 'switch':
        if value.lower() not in SWITCHES:
            message = f'{key} takes true or false.'
            raise SettingError(message)
        return SWITCHES[value.lower()]
    if kind == 'choice':
        if value not in CHOICES[key]:
            message = f'{key} takes one of: {", ".join(CHOICES[key])}.'
            raise SettingError(message)
        return value
    if kind in ('count', 'seconds'):
        number: float
        try:
            number = int(value) if kind == 'count' else float(value)
        except ValueError:
            number = -1
        if number < 0 or not math.isfinite(number) or (kind == 'seconds' and number == 0):
            what = (
                'a whole number, where 0 means no limit'
                if kind == 'count'
                else 'a number of seconds'
            )
            message = f'{key} takes {what}.'
            raise SettingError(message)
        return int(number) if kind == 'count' or float(number).is_integer() else number
    if kind == 'executable':
        found = executable(value)
        if found is None:
            message = f'{value} is not an executable file.'
            raise SettingError(message)
        # Absolute, as the service runs elsewhere; unresolved, so a venv's link is kept.
        return str(Path(found).absolute())
    return value


def default(key: str) -> Any:
    """Return a setting's default value."""
    section, _, item = key.partition('.')
    return DEFAULTS[section][item] if item else DEFAULTS[key]


def current(key: str) -> Any:
    """Return a setting's value in effect."""
    section, _, item = check_key(key).partition('.')
    config = load()
    return config[section][item] if item else config[key]


def put(key: str, value: Any) -> Path:
    """Store one setting, leaving it out of the file when it matches the default."""
    user = stored()
    section, _, item = check_key(key).partition('.')
    if item:
        nested = dict(user.get(section) or {})
        nested[item] = value
        user[section] = {k: v for k, v in nested.items() if v != DEFAULTS[section].get(k)}
        if not user[section]:
            del user[section]
    elif value == DEFAULTS[key]:
        user.pop(key, None)
    else:
        user[key] = value
    return save(user)


def save(config: dict[str, Any]) -> Path:
    """Write the configuration to disk and return its path."""
    APP_SUPPORT.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(config, indent=2) + '\n')
    return CONFIG_PATH


def executable(path: str | os.PathLike[str] | None) -> str | None:
    """Return the path as a string when it names an executable file, else None."""
    if not path:
        return None
    candidate = Path(path).expanduser()
    return str(candidate) if candidate.is_file() and os.access(candidate, os.X_OK) else None


def find_python(config: dict[str, Any] | None = None) -> str | None:
    """Return the interpreter of the configured PyVista environment."""
    return executable((config or {}).get('python'))


def resolve_pyvista(config: dict[str, Any] | None = None) -> str | None:
    """Return the ``pyvista`` command line interface beside the configured interpreter."""
    interpreter = find_python(config)
    return None if interpreter is None else executable(Path(interpreter).parent / 'pyvista')
