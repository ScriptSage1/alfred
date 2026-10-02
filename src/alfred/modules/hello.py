"""A tiny example module that proves Alfred can load modules."""

from __future__ import annotations

from alfred.modules.base import AlfredModule


class HelloModule(AlfredModule):
    name = "hello"

    def greet(self, who: str = "world") -> str:
        return f"Hello, {who}! Alfred is at your service."

    def start(self) -> None:
        self.log.info("hello module started")
        print(self.greet())


def create_module() -> AlfredModule:
    """Factory function that Alfred's loader calls to build this module."""
    return HelloModule()
