"""The Alfred Agent: one user message in, one reply out, with tools in between.

    User -> Agent.run(message) -> backend (Copilot) -> tool calls -> registry -> Python
                                       ^                                          |
                                       +------------- tool results ---------------+

The agent is generic. It knows nothing about tasks (those are just tools in
the registry) and nothing about Copilot (that is the backend, swappable).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Protocol

from alfred.tools import Tool, ToolOutcome, ToolRegistry

log = logging.getLogger(__name__)

EventKind = Literal["thinking", "tool_started", "tool_finished", "tool_failed", "reply"]


@dataclass(frozen=True)
class AgentEvent:
    """Progress report while the agent works; the UI turns these into status lines."""

    kind: EventKind
    message: str
    tool: str | None = None


class AgentError(Exception):
    """The agent could not answer: backend not signed in, unreachable, timed out, ..."""


ToolCaller = Callable[[str, Mapping[str, Any] | str | None], Awaitable[ToolOutcome]]


class AgentBackend(Protocol):
    """What the agent needs from an AI provider. CopilotBackend implements it;
    tests use a scripted fake."""

    async def complete(
        self,
        *,
        instructions: str,
        message: str,
        tools: Sequence[Tool],
        call_tool: ToolCaller,
    ) -> str:
        """Answer `message`, calling `call_tool` whenever the model wants a tool."""
        ...


INSTRUCTIONS = """\
You are Alfred, a personal desktop assistant.
- Carry out requests by calling the provided tools. When the intent is clear, act instead of asking.
- Only report facts that come from tool results. Never invent ids, dates or data.
- If a tool returns an error, fix the arguments and retry when the fix is obvious; otherwise explain the problem briefly.
- Reply in one or two short sentences of plain text (no Markdown). The reply is shown in a small command palette.
Current local date and time: {now:%A, %Y-%m-%d %H:%M}."""


class Agent:
    def __init__(
        self,
        backend: AgentBackend,
        registry: ToolRegistry,
        *,
        clock: Callable[[], datetime] = datetime.now,
    ) -> None:
        self._backend = backend
        self._registry = registry
        self._clock = clock

    async def run(self, message: str, on_event: Callable[[AgentEvent], None] | None = None) -> str:
        """Handle one user message and return Alfred's reply."""
        message = message.strip()
        if not message:
            raise AgentError("there is no message to send")

        def emit(event: AgentEvent) -> None:
            if on_event is not None:
                try:
                    on_event(event)
                except Exception:  # a broken listener must not break the agent
                    log.exception("progress listener failed")

        async def call_tool(name: str, arguments: Mapping[str, Any] | str | None) -> ToolOutcome:
            label = _label(name)
            emit(AgentEvent("tool_started", f"{label}…", tool=name))
            # Tools are ordinary (blocking) Python functions, so run them in a worker
            # thread; the event loop stays free to talk to the AI backend.
            outcome = await asyncio.to_thread(self._registry.call, name, arguments)
            if outcome.ok:
                emit(AgentEvent("tool_finished", label, tool=name))
            else:
                emit(AgentEvent("tool_failed", f"{label}: {outcome.content}", tool=name))
            return outcome

        log.info("agent received a message (%d characters)", len(message))
        emit(AgentEvent("thinking", "Thinking…"))
        reply = await self._backend.complete(
            instructions=INSTRUCTIONS.format(now=self._clock()),
            message=message,
            tools=list(self._registry),
            call_tool=call_tool,
        )
        reply = reply.strip() or "Done."
        emit(AgentEvent("reply", reply))
        return reply


def _label(tool_name: str) -> str:
    """'create_task' -> 'Create task'."""
    words = tool_name.replace("-", "_").split("_")
    return " ".join(words).capitalize()
