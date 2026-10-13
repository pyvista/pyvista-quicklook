"""Import PyVista and VTK ahead of time so the first preview does not pay for them."""

from __future__ import annotations

from pathlib import Path
import time
from typing import Any

from . import config as config_mod
from . import environment

SCRIPT = Path(__file__).with_name('_preload.py')
TIMEOUT = 300
RECENT_SECONDS = 120


def stamp_path() -> Path:
    """Return the file whose modification time records the last preload."""
    return config_mod.CACHE_DIR / '.preloaded'


def preloaded_recently(within: float = RECENT_SECONDS) -> bool:
    """Return whether a preload finished within the given number of seconds."""
    try:
        return time.time() - stamp_path().stat().st_mtime < within
    except OSError:
        return False


def run(config: dict[str, Any] | None = None) -> float:
    """Load PyVista and VTK in the configured environment and return how long it took."""
    config = config if config is not None else config_mod.load()
    command = [environment.interpreter(config), str(SCRIPT)]
    started = time.monotonic()
    environment.run(config, command, task='Preloading PyVista', timeout=TIMEOUT)
    elapsed = time.monotonic() - started

    try:
        config_mod.CACHE_DIR.mkdir(parents=True, exist_ok=True)
        stamp_path().touch()
    except OSError:
        pass

    environment.log(config, f'preloaded in {elapsed:.1f}s')
    return elapsed
