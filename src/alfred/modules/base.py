"""The contract every Alfred module must follow.

A module file (e.g. modules/hello.py) must:

1. define a subclass of AlfredModule that implements start(), and
2. expose a function ``create_module()`` that returns an instance of it.

Alfred never imports a module class by name. It only calls create_module(),
so the core app does not need to change when a new module is added.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod


class AlfredModule(ABC):
    """Base class for all Alfred modules."""

    #: Short unique name, matching the file name (e.g. "hello").
    name: str = ""

    def __init__(self) -> None:
        self.log = logging.getLogger(f"alfred.modules.{self.name}")

    @abstractmethod
    def start(self) -> None:
        """Called once by Alfred after the module has been loaded."""
