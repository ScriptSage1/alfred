"""Wire Alfred together. This is the "composition root": the one place that
knows every concrete piece and plugs them into each other.

    ObsidianClient -> TodoService -> todo tools -> ToolRegistry
                                                        |
                         CopilotBackend ----------> Agent

Everything else depends only on small interfaces: the Agent sees "a backend"
and "a registry", the UI sees "something with run(text)".
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from alfred.agent import Agent
from alfred.agent.copilot_backend import CopilotBackend
from alfred.config import AlfredConfig
from alfred.obsidian import ObsidianClient
from alfred.todo import TodoService
from alfred.todo.tools import register_todo_tools
from alfred.tools import ToolRegistry


def build_registry(todos: TodoService) -> ToolRegistry:
    """All the tools Alfred offers. New features register their tools here."""
    registry = ToolRegistry()
    register_todo_tools(registry, todos)
    return registry


@asynccontextmanager
async def open_agent(config: AlfredConfig) -> AsyncIterator[Agent]:
    """Start everything the agent needs, and shut it all down afterwards.

        async with open_agent(config) as agent:
            reply = await agent.run("Add a task to buy milk")
    """
    with ObsidianClient.from_settings(config.obsidian) as obsidian:
        registry = build_registry(TodoService(obsidian))
        async with CopilotBackend(model=config.copilot.model, timeout=config.copilot.timeout) as backend:
            yield Agent(backend, registry)
