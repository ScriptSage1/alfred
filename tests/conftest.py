"""Shared test helpers. pytest loads this file automatically."""

from datetime import datetime, timedelta

import pytest

from alfred.obsidian import NoteAlreadyExistsError, NoteNotFoundError
from alfred.todo import TodoService


class InMemoryVault:
    """Pretends to be ObsidianClient, keeping notes in a dict instead of a real vault.

    It raises the same exceptions as the real client, so TodoService cannot
    tell the difference.
    """

    def __init__(self) -> None:
        self.notes: dict[str, str] = {}

    def read_note(self, path: str) -> str:
        if path not in self.notes:
            raise NoteNotFoundError(path)
        return self.notes[path]

    def create_note(self, path: str, content: str) -> None:
        if path in self.notes:
            raise NoteAlreadyExistsError(path)
        self.notes[path] = content

    def update_note(self, path: str, content: str) -> None:
        if path not in self.notes:
            raise NoteNotFoundError(path)
        self.notes[path] = content

    def delete_note(self, path: str) -> None:
        if path not in self.notes:
            raise NoteNotFoundError(path)
        del self.notes[path]

    def list_notes(self, folder: str) -> list[str]:
        prefix = folder.strip("/") + "/"
        return [p for p in self.notes if p.startswith(prefix) and "/" not in p[len(prefix):]]


class FakeClock:
    """A clock that only moves when the test says so."""

    def __init__(self, start: datetime = datetime(2026, 10, 5, 9, 30)) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs: float) -> None:
        self.now += timedelta(**kwargs)


class ScriptedBackend:
    """Plays the part of Copilot: makes the tool calls it was given, then replies.

    The real model decides which tools to call; here the test decides, so we
    can check everything that happens *after* the model's decision.
    """

    def __init__(self, calls=(), reply: str = "Done!") -> None:
        self.calls = list(calls)
        self.reply = reply
        self.outcomes = []
        self.seen: dict = {}

    async def complete(self, *, instructions, message, tools, call_tool) -> str:
        self.seen = {"instructions": instructions, "message": message, "tools": [t.name for t in tools]}
        for name, arguments in self.calls:
            self.outcomes.append(await call_tool(name, arguments))
        return self.reply


@pytest.fixture
def vault() -> InMemoryVault:
    return InMemoryVault()


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def service(vault: InMemoryVault, clock: FakeClock) -> TodoService:
    return TodoService(vault, clock=clock)
