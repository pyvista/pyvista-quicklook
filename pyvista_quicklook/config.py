"""Configuration loading for the Quick Look helper."""

from __future__ import annotations

import contextlib
import json
import math
import os
from pathlib import Path
from typing import Any

from .plist import EXT_BUNDLE_ID

APP_SUPPORT = Path.home() / 'Library' / 'Application Support' / 'PyVistaQuickLook'
CONFIG_PATH = APP_SUPPORT / 'config.json'
# The copy the sandboxed extension can read, in the folder it exchanges requests through.
MIRROR_PATH = (
    Path.home() / 'Library' / 'Containers' / EXT_BUNDLE_ID / 'Data' / 'tmp' / 'config.json'
)
CACHE_DIR = Path.home() / 'Library' / 'Caches' / 'PyVistaQuickLook'
LOG_PATH = APP_SUPPORT / 'pvql.log'

DEFAULTS: dict[str, Any] = {
    'python': None,
    'pvql': None,
    'interactive': True,
    # Whether a .pv file opens on the 3D view even when it carries a picture of itself.
    'force_interactive': False,
    'info_panel': 'automatic',
    'preload_on_start': True,
    'max_scene_points': 2000000,
    'window_size': [1024, 1024],
    'timeout': 60,
    'max_file_size_mb': 2048,
    # None leaves half the Mac's memory to previews being built at once.
    'max_conversion_memory_mb': None,
    'background': None,
    # None leaves PyVista's own theme; otherwise a theme's name or a theme saved as JSON.
    'theme': None,
    'extra_args': [],
    'extensions': {'add': [], 'remove': []},
    # Whether to preview .ply, .stl, .obj, USD and .abc in place of macOS's own 3D viewer.
    'native_formats': False,
    'cache': True,
    # How often the service drops previews of files that have changed or gone; 0 never.
    'cache_check_minutes': 1440,
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
            if 'warm_on_start' in user:  # the key's former name
                user.setdefault('preload_on_start', user.pop('warm_on_start'))
            config.update({k: coerced(k, v) for k, v in user.items() if k in DEFAULTS})
    config['extensions'] = {**DEFAULTS['extensions'], **config['extensions']}
    return config


def coerced(key: str, value: Any) -> Any:
    """Return a stored value when it is what the setting takes, else the default."""
    if key == 'extensions':
        # Set through extensions.add and extensions.remove, but stored as one table.
        if not isinstance(value, dict):
            return DEFAULTS[key]
        return {k: v for k, v in value.items() if isinstance(v, list)}
    kind = KINDS.get(key)
    if value is None or kind is None:
        return value if kind is None else DEFAULTS[key]
    if kind == 'switch':
        return value if isinstance(value, bool) else DEFAULTS[key]
    if kind == 'choice':
        return value if value in CHOICES[key] else DEFAULTS[key]
    if kind == 'pixels':
        sides = value if isinstance(value, list) else []
        if len(sides) == 2 and all(isinstance(s, int) and s > 0 for s in sides):
            return sides
        return DEFAULTS[key]
    if kind in ('count', 'minutes', 'seconds'):
        number = value if isinstance(value, (int, float)) and not isinstance(value, bool) else -1
        if number < 0 or not math.isfinite(number) or (kind == 'seconds' and number == 0):
            return DEFAULTS[key]
        return number
    if kind == 'words':
        return value if isinstance(value, list) else DEFAULTS[key]
    return value if isinstance(value, str) else DEFAULTS[key]


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
    'force_interactive': 'switch',
    'info_panel': 'choice',
    'preload_on_start': 'switch',
    'max_scene_points': 'count',
    'window_size': 'pixels',
    'timeout': 'seconds',
    'max_file_size_mb': 'count',
    'max_conversion_memory_mb': 'count',
    'background': 'text',
    'theme': 'theme',
    'extra_args': 'words',
    'extensions.add': 'extensions',
    'extensions.remove': 'extensions',
    'native_formats': 'switch',
    'cache': 'switch',
    'cache_check_minutes': 'minutes',
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
    if kind in ('count', 'minutes', 'seconds'):
        number: float
        try:
            number = float(value) if kind == 'seconds' else int(value)
        except ValueError:
            number = -1
        if number < 0 or not math.isfinite(number) or (kind == 'seconds' and number == 0):
            what = {
                'count': 'a whole number, where 0 means no limit',
                'minutes': 'a whole number of minutes, where 0 means never',
                'seconds': 'a number of seconds',
            }[kind]
            message = f'{key} takes {what}.'
            raise SettingError(message)
        return number if kind == 'seconds' and not float(number).is_integer() else int(number)
    if kind == 'theme':
        return theme_spec(value)
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
    """Write the configuration to disk, copy it for the extension, and return its path."""
    APP_SUPPORT.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(config, indent=2) + '\n')
    mirror()
    return CONFIG_PATH


def mirror() -> None:
    """Keep the extension's copy of the settings the same as the file, or gone without one."""
    try:
        settings = CONFIG_PATH.read_bytes()
    except OSError:
        MIRROR_PATH.unlink(missing_ok=True)
        return
    with contextlib.suppress(OSError):
        if MIRROR_PATH.read_bytes() == settings:
            return
    MIRROR_PATH.parent.mkdir(parents=True, exist_ok=True)
    MIRROR_PATH.write_bytes(settings)


def saved_theme(config: dict[str, Any]) -> bool:
    """Return whether the theme is one saved as JSON, which only the 3D view can follow."""
    return bool(config.get('theme')) and os.path.isabs(config['theme'])


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


def theme_spec(value: str) -> str:
    """Return a theme's name as given, or the absolute path of a theme saved as JSON."""
    if not (value.lower().endswith('.json') or os.sep in value or value.startswith('~')):
        return value
    path = Path(value).expanduser()
    if not path.is_file():
        message = f'{value} is not a file; theme takes a theme name or a theme saved as JSON.'
        raise SettingError(message)
    return str(path.absolute())


def theme_token(config: dict[str, Any]) -> str:
    """Return what a cache key holds of the theme, so editing a theme file redraws previews."""
    spec = config.get('theme')
    if not spec:
        return repr(None)
    try:
        stat = Path(spec).stat() if os.path.isabs(spec) else None
    except OSError:
        stat = None
    return repr((spec, stat and stat.st_mtime_ns, stat and stat.st_size))
