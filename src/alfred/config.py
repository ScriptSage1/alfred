"""Configuration handling for Alfred.

Settings come from three places, from lowest to highest priority:

1. Defaults defined on the AlfredConfig dataclass below.
2. A TOML file (alfred.toml in the current directory by default).
3. Command-line overrides, applied in __main__.py.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

DEFAULT_CONFIG_PATH = Path("alfred.toml")
VALID_LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")


class ConfigError(Exception):
    """Raised when the configuration is missing or invalid."""


@dataclass(frozen=True)
class AlfredConfig:
    """All of Alfred's settings. frozen=True makes it read-only once created."""

    name: str = "Alfred"
    log_level: str = "INFO"
    modules: tuple[str, ...] = ("hello",)


def load_config(path: Path | None = None) -> AlfredConfig:
    """Build an AlfredConfig from a TOML file.

    If ``path`` is None, use ./alfred.toml when it exists, otherwise the
    built-in defaults. If ``path`` is given explicitly, it must exist.
    """
    if path is None:
        if not DEFAULT_CONFIG_PATH.exists():
            return AlfredConfig()
        path = DEFAULT_CONFIG_PATH
    elif not path.exists():
        raise ConfigError(f"config file not found: {path}")

    try:
        with path.open("rb") as f:  # tomllib requires binary mode
            data = tomllib.load(f)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"invalid TOML in {path}: {exc}") from exc

    return _from_dict(data.get("alfred", {}))


def _from_dict(section: dict) -> AlfredConfig:
    """Validate the [alfred] section and turn it into an AlfredConfig."""
    defaults = AlfredConfig()

    name = section.get("name", defaults.name)
    if not isinstance(name, str):
        raise ConfigError("'name' must be a string")

    log_level = str(section.get("log_level", defaults.log_level)).upper()
    if log_level not in VALID_LOG_LEVELS:
        raise ConfigError(
            f"'log_level' must be one of {', '.join(VALID_LOG_LEVELS)}, got {log_level!r}"
        )

    modules = section.get("modules", list(defaults.modules))
    if not isinstance(modules, list) or not all(isinstance(m, str) for m in modules):
        raise ConfigError("'modules' must be a list of strings")

    return AlfredConfig(name=name, log_level=log_level, modules=tuple(modules))
