"""Command-line entry point.

Runs when you type `alfred` (installed by pyproject.toml) or `python -m alfred`.
"""

from __future__ import annotations

import argparse
import dataclasses
import logging
import sys
from pathlib import Path

from alfred import __version__
from alfred.app import Alfred, ModuleLoadError
from alfred.config import VALID_LOG_LEVELS, ConfigError, load_config
from alfred.logging_setup import configure_logging


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="alfred", description="Alfred, your personal desktop assistant."
    )
    parser.add_argument(
        "--config",
        type=Path,
        help="path to a TOML config file (default: ./alfred.toml if it exists)",
    )
    parser.add_argument(
        "--log-level",
        choices=VALID_LOG_LEVELS,
        help="override the log level from the config file",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Start Alfred. Returns a process exit code (0 means success)."""
    args = parse_args(argv)

    try:
        config = load_config(args.config)
    except ConfigError as exc:
        # Logging isn't configured yet, so report straight to stderr.
        print(f"alfred: {exc}", file=sys.stderr)
        return 2

    if args.log_level:
        config = dataclasses.replace(config, log_level=args.log_level)

    configure_logging(config.log_level)

    try:
        Alfred(config).run()
    except ModuleLoadError as exc:
        logging.getLogger("alfred").error("%s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
