"""Configuration loading for the Quick Look helper."""

from __future__ import annotations

import json
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
