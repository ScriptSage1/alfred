"""Configuration handling for Alfred.

Settings come from four places, from lowest to highest priority:

1. Defaults defined on the dataclasses below.
2. A TOML file (alfred.toml in the current directory by default).
3. Environment variables (OBSIDIAN_URL, OBSIDIAN_API_KEY, OBSIDIAN_CA_CERT).
   (The Copilot runtime reads COPILOT_GITHUB_TOKEN itself.)
4. Command-line overrides, applied in __main__.py.

Secrets (API keys, tokens) are only ever read from environment variables,
never from the TOML file, because config files tend to end up in git.
"""

from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_CONFIG_PATH = Path("alfred.toml")
VALID_LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")


class ConfigError(Exception):
    """Raised when the configuration is missing or invalid."""


@dataclass(frozen=True)
class ObsidianSettings:
    """How to reach the Obsidian Local REST API plugin."""

    url: str = "https://127.0.0.1:27124"
    # repr=False keeps the key out of print(settings), logs and error messages.
    api_key: str | None = field(default=None, repr=False)
    # The plugin's certificate authority (PEM file), needed to verify HTTPS.
    ca_cert: Path | None = None
    timeout: float = 10.0


@dataclass(frozen=True)
class CopilotSettings:
    """How Alfred uses GitHub Copilot. The token comes from COPILOT_GITHUB_TOKEN,
    which the Copilot runtime reads itself; Alfred never handles it."""

    model: str | None = None  # None: Copilot's default model
    timeout: float = 120.0    # seconds to wait for an answer


@dataclass(frozen=True)
class UISettings:
    hotkey: str = "ctrl+alt+space"


@dataclass(frozen=True)
class AlfredConfig:
    """All of Alfred's settings. frozen=True makes it read-only once created."""

    name: str = "Alfred"
    log_level: str = "INFO"
    modules: tuple[str, ...] = ("hello",)
    obsidian: ObsidianSettings = field(default_factory=ObsidianSettings)
    copilot: CopilotSettings = field(default_factory=CopilotSettings)
    ui: UISettings = field(default_factory=UISettings)


def load_config(path: Path | None = None, env: Mapping[str, str] | None = None) -> AlfredConfig:
    """Build an AlfredConfig from a TOML file and environment variables.

    If ``path`` is None, use ./alfred.toml when it exists, otherwise the
    built-in defaults. If ``path`` is given explicitly, it must exist.
    ``env`` defaults to the real environment; tests pass a plain dict instead.
    """
    if env is None:
        env = os.environ

    data: dict = {}
    if path is None and DEFAULT_CONFIG_PATH.exists():
        path = DEFAULT_CONFIG_PATH
    if path is not None:
        data = _read_toml(path)

    return AlfredConfig(
        **_alfred_section(data.get("alfred", {})),
        obsidian=_obsidian_section(data.get("obsidian", {}), env),
        copilot=_copilot_section(data.get("copilot", {})),
        ui=_ui_section(data.get("ui", {})),
    )


def _read_toml(path: Path) -> dict:
    if not path.exists():
        raise ConfigError(f"config file not found: {path}")
    try:
        with path.open("rb") as f:  # tomllib requires binary mode
            return tomllib.load(f)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"invalid TOML in {path}: {exc}") from exc


def _alfred_section(section: dict) -> dict:
    """Validate the [alfred] section and return AlfredConfig keyword arguments."""
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

    return {"name": name, "log_level": log_level, "modules": tuple(modules)}


def _obsidian_section(section: dict, env: Mapping[str, str]) -> ObsidianSettings:
    """Validate the [obsidian] section, then apply environment variable overrides."""
    defaults = ObsidianSettings()

    if "api_key" in section:
        raise ConfigError(
            "do not put the Obsidian API key in the config file; "
            "set the OBSIDIAN_API_KEY environment variable instead"
        )

    url = env.get("OBSIDIAN_URL") or section.get("url", defaults.url)
    if not isinstance(url, str) or not url.startswith(("http://", "https://")):
        raise ConfigError("obsidian 'url' must start with http:// or https://")

    ca_cert = env.get("OBSIDIAN_CA_CERT") or section.get("ca_cert")
    if ca_cert is not None and not isinstance(ca_cert, str):
        raise ConfigError("obsidian 'ca_cert' must be a file path")

    timeout = section.get("timeout", defaults.timeout)
    if not isinstance(timeout, (int, float)) or timeout <= 0:
        raise ConfigError("obsidian 'timeout' must be a positive number of seconds")

    return ObsidianSettings(
        url=url.rstrip("/"),
        api_key=env.get("OBSIDIAN_API_KEY") or None,
        ca_cert=Path(ca_cert) if ca_cert else None,
        timeout=float(timeout),
    )


def _copilot_section(section: dict) -> CopilotSettings:
    defaults = CopilotSettings()
    if {"token", "github_token"} & set(section):
        raise ConfigError(
            "do not put a GitHub token in the config file; "
            "set the COPILOT_GITHUB_TOKEN environment variable instead"
        )

    model = section.get("model") or None
    if model is not None and not isinstance(model, str):
        raise ConfigError("copilot 'model' must be text")

    timeout = section.get("timeout", defaults.timeout)
    if not isinstance(timeout, (int, float)) or timeout <= 0:
        raise ConfigError("copilot 'timeout' must be a positive number of seconds")

    return CopilotSettings(model=model, timeout=float(timeout))


def _ui_section(section: dict) -> UISettings:
    hotkey = section.get("hotkey", UISettings().hotkey)
    if not isinstance(hotkey, str) or not hotkey.strip():
        raise ConfigError("ui 'hotkey' must be text like \"ctrl+alt+space\"")
    return UISettings(hotkey=hotkey.strip().lower())
