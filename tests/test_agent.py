import asyncio
from datetime import datetime

import pytest
from pydantic import BaseModel

from alfred.agent import Agent, AgentError, AgentEvent
from alfred.tools import Tool, ToolRegistry
from conftest import ScriptedBackend


class EchoParams(BaseModel):
    text: str


def make_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(Tool("echo_text", "Echo text back.", EchoParams, lambda p: {"echo": p.text}))
    return registry


def run(agent: Agent, message: str, events: list | None = None) -> str:
    return asyncio.run(agent.run(message, on_event=events.append if events is not None else None))


def test_agent_returns_the_backend_reply():
    backend = ScriptedBackend(reply="Hello there.")
    assert run(Agent(backend, make_registry()), "hi") == "Hello there."
    assert backend.seen["message"] == "hi"


def test_agent_offers_all_registered_tools():
    backend = ScriptedBackend()
    run(Agent(backend, make_registry()), "hi")
    assert backend.seen["tools"] == ["echo_text"]


def test_instructions_include_the_current_date():
    backend = ScriptedBackend()
    agent = Agent(backend, make_registry(), clock=lambda: datetime(2026, 10, 5, 14, 3))
    run(agent, "hi")
    assert "Monday, 2026-10-05 14:03" in backend.seen["instructions"]


def test_tool_calls_go_through_the_registry_and_report_progress():
    backend = ScriptedBackend(calls=[("echo_text", {"text": "ping"})], reply="Pong.")
    events: list[AgentEvent] = []

    reply = run(Agent(backend, make_registry()), "say ping", events)

    assert reply == "Pong."
    assert backend.outcomes[0].ok
    assert backend.outcomes[0].content == '{"echo": "ping"}'
    assert [(e.kind, e.message) for e in events] == [
        ("thinking", "Thinking…"),
        ("tool_started", "Echo text…"),
        ("tool_finished", "Echo text"),
        ("reply", "Pong."),
    ]


def test_failed_tool_call_is_reported_and_returned_to_the_model():
    backend = ScriptedBackend(calls=[("echo_text", {"wrong": 1})])
    events: list[AgentEvent] = []

    run(Agent(backend, make_registry()), "go", events)

    assert not backend.outcomes[0].ok
    failed = [e for e in events if e.kind == "tool_failed"]
    assert len(failed) == 1
    assert failed[0].tool == "echo_text"
    assert "Invalid arguments" in failed[0].message


def test_empty_reply_becomes_done():
    assert run(Agent(ScriptedBackend(reply="  "), make_registry()), "hi") == "Done."


def test_empty_message_is_rejected():
    with pytest.raises(AgentError):
        run(Agent(ScriptedBackend(), make_registry()), "   ")


def test_a_broken_progress_listener_does_not_break_the_agent():
    def explode(event):
        raise RuntimeError("listener bug")

    agent = Agent(ScriptedBackend(reply="Still fine."), make_registry())
    assert asyncio.run(agent.run("hi", on_event=explode)) == "Still fine."
