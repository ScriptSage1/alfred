"""The task tools, exercised the way Copilot would use them.

Copilot turns "Remind me to finish the SIH documentation by Friday and remind me
every day" into a structured create_task call. We can't test the model's
judgement here, but we can test everything that happens after it decides.
"""

import asyncio
import json
from datetime import date, datetime

import pytest

from alfred.agent import Agent
from alfred.todo import ReminderPolicy, Status, parse_todo
from alfred.todo.tools import describe_reminder, register_todo_tools
from alfred.tools import ToolRegistry
from conftest import ScriptedBackend

MONDAY = datetime(2026, 10, 5, 9, 30)


@pytest.fixture
def registry(service) -> ToolRegistry:
    registry = ToolRegistry()
    register_todo_tools(registry, service)
    return registry


def call(registry, name, **arguments):
    outcome = registry.call(name, arguments)
    return outcome.ok, json.loads(outcome.content) if outcome.ok else outcome.content


def test_the_five_tools_are_registered(registry):
    assert [t.name for t in registry] == [
        "create_task", "get_task", "search_tasks", "update_task", "complete_task"
    ]


def test_sih_example_end_to_end(service, vault):
    """User -> Agent -> (Copilot's tool call) -> create_task -> TodoService -> vault."""
    registry = ToolRegistry()
    register_todo_tools(registry, service)
    copilot = ScriptedBackend(
        calls=[("create_task", {
            "title": "Finish the SIH documentation",
            "deadline": "2026-10-09",   # "by Friday", worked out from the date in the instructions
            "reminder": "daily",        # "remind me every day"
        })],
        reply="Added 'Finish the SIH documentation', due Friday, with a daily reminder.",
    )
    agent = Agent(copilot, registry, clock=lambda: MONDAY)

    reply = asyncio.run(agent.run(
        "Remind me to finish the SIH documentation by Friday and remind me every day."
    ))

    assert "Monday, 2026-10-05" in copilot.seen["instructions"]
    assert copilot.outcomes[0].ok
    assert reply.startswith("Added")

    # Python, not the model, produced this file:
    text = vault.notes["To-do/finish-the-sih-documentation.md"]
    assert text.startswith('---\nid: "finish-the-sih-documentation"\n')
    todo = parse_todo(text)
    assert todo.deadline == date(2026, 10, 9)
    assert describe_reminder(todo.reminder) == "daily at 09:00"


def test_create_task_returns_a_summary_not_markdown(registry):
    ok, result = call(registry, "create_task", title="Buy milk", tags=["shopping"], priority="high")
    assert ok
    assert result["file"] == "To-do/buy-milk.md"
    assert result["created"]["id"] == "buy-milk"
    assert result["created"]["priority"] == "high"
    assert "---" not in json.dumps(result)


@pytest.mark.parametrize(
    "arguments, message",
    [
        ({"title": ""}, "title"),
        ({"title": "x", "priority": "urgent"}, "priority"),
        ({"title": "x", "deadline": "friday"}, "deadline"),
        ({"title": "x", "reminder_time": "9am"}, "reminder_time"),
        ({"title": "x", "markdown": "---\nid: evil\n---"}, "markdown"),  # no free-form Markdown
        ({"title": "x", "reminder": "weekly"}, "weekday"),               # caught by the Todo model
        ({"title": "x", "related_tasks": ["ghost"]}, "does not exist"),
        ({"title": "Line one\n---\nid: evil"}, "single line"),
    ],
)
def test_create_task_rejects_bad_arguments(registry, vault, arguments, message):
    outcome = registry.call("create_task", arguments)
    assert not outcome.ok
    assert message in outcome.content
    assert vault.notes == {}


def test_get_task(registry):
    call(registry, "create_task", title="Buy milk", reminder="weekly", reminder_days=["thu", "mon"])
    ok, task = call(registry, "get_task", task_id="buy-milk")
    assert ok
    assert task["reminder"] == "weekly on mon, thu at 09:00"


def test_get_missing_task(registry):
    ok, message = call(registry, "get_task", task_id="nope")
    assert not ok
    assert "no task with id 'nope'" in message


def test_search_tasks(registry):
    call(registry, "create_task", title="Finish SIH documentation", tags=["sih"])
    call(registry, "create_task", title="Prepare SIH slides", tags=["sih"])
    call(registry, "create_task", title="Buy milk")

    ok, found = call(registry, "search_tasks", query="sih docu")
    assert ok
    assert found["count"] == 1
    assert found["tasks"][0]["id"] == "finish-sih-documentation"

    ok, everything = call(registry, "search_tasks")
    assert everything["count"] == 3

    ok, tagged = call(registry, "search_tasks", tag="sih", status="open")
    assert {t["id"] for t in tagged["tasks"]} == {"finish-sih-documentation", "prepare-sih-slides"}


def test_update_only_changes_the_fields_given(registry, service):
    call(registry, "create_task", title="Buy milk", deadline="2026-10-09", priority="low", reminder="daily")
    ok, result = call(registry, "update_task", task_id="buy-milk", priority="high")
    assert ok
    todo = service.get_task("buy-milk")
    assert todo.priority == "high"
    assert todo.deadline == date(2026, 10, 9)        # untouched
    assert todo.reminder == ReminderPolicy("daily")   # untouched


def test_update_can_clear_the_deadline(registry, service):
    call(registry, "create_task", title="Buy milk", deadline="2026-10-09")
    call(registry, "update_task", task_id="buy-milk", deadline=None)
    assert service.get_task("buy-milk").deadline is None


def test_update_reminder_time_keeps_the_frequency(registry, service):
    call(registry, "create_task", title="Buy milk", reminder="daily")
    call(registry, "update_task", task_id="buy-milk", reminder_time="18:30")
    assert describe_reminder(service.get_task("buy-milk").reminder) == "daily at 18:30"


def test_update_with_nothing_to_change(registry):
    call(registry, "create_task", title="Buy milk")
    ok, message = call(registry, "update_task", task_id="buy-milk")
    assert not ok
    assert "nothing to update" in message


def test_complete_task(registry, service):
    call(registry, "create_task", title="Buy milk")
    ok, result = call(registry, "complete_task", task_id="buy-milk")
    assert ok
    assert result["completed"]["status"] == "done"
    assert service.get_task("buy-milk").status is Status.DONE
