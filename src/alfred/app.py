"""The Alfred application: loads the configured modules and starts them."""

from __future__ import annotations

import importlib
import logging

from alfred.config import AlfredConfig
from alfred.modules.base import AlfredModule

log = logging.getLogger(__name__)

MODULES_PACKAGE = "alfred.modules"


class ModuleLoadError(Exception):
    """Raised when a module listed in the config cannot be loaded."""


def load_module(name: str) -> AlfredModule:
    """Import ``alfred.modules.<name>`` and return the object its create_module() builds."""
    if not name.isidentifier():
        raise ModuleLoadError(f"invalid module name {name!r}")

    import_path = f"{MODULES_PACKAGE}.{name}"
    try:
        py_module = importlib.import_module(import_path)
    except ModuleNotFoundError as exc:
        if exc.name != import_path:
            raise  # the module file exists, but something *it* imports is missing
        raise ModuleLoadError(f"no Alfred module named {name!r}") from exc

    factory = getattr(py_module, "create_module", None)
    if not callable(factory):
        raise ModuleLoadError(f"{import_path} has no create_module() function")

    module = factory()
    if not isinstance(module, AlfredModule):
        raise ModuleLoadError(f"{import_path}.create_module() did not return an AlfredModule")
    return module


class Alfred:
    """The application object. Owns the config and the loaded modules."""

    def __init__(self, config: AlfredConfig) -> None:
        self.config = config
        self.modules: dict[str, AlfredModule] = {}

    def load_modules(self) -> None:
        for name in self.config.modules:
            self.modules[name] = load_module(name)
            log.info("loaded module %r", name)

    def run(self) -> None:
        log.info("%s is starting", self.config.name)
        self.load_modules()
        for module in self.modules.values():
            module.start()
        log.info("%s is ready with %d module(s)", self.config.name, len(self.modules))
