"""Logging setup for Alfred.

This file is deliberately NOT named logging.py: that would shadow Python's
own `logging` module and break every `import logging` in the project.
"""

from __future__ import annotations

import logging

LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"
DATE_FORMAT = "%H:%M:%S"


def configure_logging(level: str = "INFO") -> None:
    """Send log messages at ``level`` and above to the console (stderr).

    Call this once, at startup. Every other file just does
    ``logging.getLogger(__name__)`` and logs; it never configures anything.
    """
    logging.basicConfig(
        level=level,
        format=LOG_FORMAT,
        datefmt=DATE_FORMAT,
        force=True,  # replace any handlers set up earlier, so calling twice is safe
    )
