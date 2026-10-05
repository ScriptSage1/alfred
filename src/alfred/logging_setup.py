"""Logging setup for Alfred.

This file is deliberately NOT named logging.py: that would shadow Python's
own `logging` module and break every `import logging` in the project.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"
DATE_FORMAT = "%H:%M:%S"


def default_log_file() -> Path:
    """%LOCALAPPDATA%\\Alfred\\alfred.log: the desktop app has no console to print to."""
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    return Path(base) / "Alfred" / "alfred.log"


def configure_logging(level: str = "INFO", log_file: Path | None = None) -> None:
    """Send log messages at ``level`` and above to the console and, optionally, a file.

    Call this once, at startup. Every other file just does
    ``logging.getLogger(__name__)`` and logs; it never configures anything.
    """
    handlers: list[logging.Handler] = []
    if sys.stderr is not None:  # pythonw (no console) has no stderr
        handlers.append(logging.StreamHandler())
    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
    logging.basicConfig(
        level=level,
        format=LOG_FORMAT,
        datefmt=DATE_FORMAT,
        handlers=handlers or [logging.NullHandler()],
        force=True,  # replace any handlers set up earlier, so calling twice is safe
    )
