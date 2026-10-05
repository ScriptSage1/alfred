"""Todo tools: the task operations an AI model may call.

Each tool is a Pydantic model (the arguments the model must provide) plus a
small handler that calls TodoService. The model never writes Markdown and
never talks to Obsidian: it fills in structured fields, and Python does the
rest (validate -> Todo -> render_todo -> ObsidianClient).

The Field descriptions below are written for the model; they are how it
learns, for example, that "by Friday" should become a YYYY-MM-DD deadline.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from functools import wraps
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from alfred.obsidian import ObsidianError
from alfred.todo.errors import TodoError
from alfred.todo.model import ReminderPolicy, Todo
from alfred.todo.service import TodoService
from alfred.tools import Tool, ToolError, ToolRegistry

PriorityName = Literal["low", "medium", "high"]
StatusName = Literal["open", "in-progress", "done", "cancelled"]
FrequencyName = Literal["none", "once", "daily", "weekly"]
Weekday = Literal["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
MAX_SEARCH_RESULTS = 50

_DEADLINE = (
    "Due date as YYYY-MM-DD. Convert relative dates ('tomorrow', 'by Friday', "
    "'next week') using the current date from your instructions."
)
_REMINDER = (
    "How often to remind the user: 'none', 'once' (needs reminder_date), "
    "'daily', or 'weekly' (needs reminder_days)."
)


class _Params(BaseModel):
    # Reject arguments we did not define, so a model cannot slip in extra fields.
    model_config = ConfigDict(extra="forbid")


class CreateTaskParams(_Params):
    title: str = Field(
        min_length=1,
        max_length=200,
        description="Short task title without dates or reminder wording, "
        "e.g. 'Finish the SIH documentation'.",
    )
    details: str = Field("", description="Optional extra notes, as plain text.")
    deadline: date | None = Field(None, description=_DEADLINE)
    priority: PriorityName = Field("medium", description="Task priority.")
    tags: list[str] = Field(default_factory=list, description="Optional tags without '#'.")
    reminder: FrequencyName = Field("none", description=_REMINDER)
    reminder_time: str | None = Field(
        None, pattern=r"^\d{1,2}:\d{2}$", description="Reminder time of day, HH:MM (24h). Default 09:00."
    )
    reminder_date: date | None = Field(None, description="Date of a 'once' reminder, YYYY-MM-DD.")
    reminder_days: list[Weekday] = Field(default_factory=list, description="Days of a 'weekly' reminder.")
    related_tasks: list[str] = Field(default_factory=list, description="Ids of existing related tasks.")


class TaskIdParams(_Params):
    task_id: str = Field(description="The task id, e.g. 'finish-sih-documentation'. Use search_tasks to find it.")


class SearchTasksParams(_Params):
    query: str = Field("", description="Words to look for in task titles, details and tags. Empty: all tasks.")
    status: StatusName | None = Field(None, description="Only tasks with this status.")
    priority: PriorityName | None = Field(None, description="Only tasks with this priority.")
    tag: str | None = Field(None, description="Only tasks with this tag.")


class UpdateTaskParams(_Params):
    """Every field except task_id is optional: only the fields given are changed."""

    task_id: str = Field(description="The id of the task to change.")
    title: str | None = Field(None, min_length=1, max_length=200, description="New title.")
    details: str | None = Field(None, description="New details (replaces the old ones).")
    deadline: date | None = Field(None, description=_DEADLINE + " Pass null to remove the deadline.")
    status: StatusName | None = Field(None, description="New status.")
    priority: PriorityName | None = Field(None, description="New priority.")
    tags: list[str] | None = Field(None, description="New tags (replaces the old ones).")
    reminder: FrequencyName | None = Field(None, description=_REMINDER)
    reminder_time: str | None = Field(None, pattern=r"^\d{1,2}:\d{2}$", description="Reminder time, HH:MM.")
    reminder_date: date | None = Field(None, description="Date of a 'once' reminder.")
    reminder_days: list[Weekday] | None = Field(None, description="Days of a 'weekly' reminder.")
    related_tasks: list[str] | None = Field(None, description="Ids of related tasks (replaces the old ones).")


_REMINDER_FIELDS = {"reminder", "reminder_time", "reminder_date", "reminder_days"}
_PLAIN_FIELDS = {"title", "details", "deadline", "status", "priority", "tags", "related_tasks"}


def register_todo_tools(registry: ToolRegistry, todos: TodoService) -> None:
    """Add the task tools to `registry`, all backed by `todos`."""

    @_tool_errors
    def create_task(p: CreateTaskParams) -> dict[str, Any]:
        todo = todos.create_task(
            p.title,
            details=p.details,
            deadline=p.deadline,
            priority=p.priority,
            tags=p.tags,
            reminder=ReminderPolicy(p.reminder, p.reminder_time, p.reminder_date, p.reminder_days),
            related_tasks=p.related_tasks,
        )
        return {"created": describe(todo), "file": f"{todos.folder}/{todo.id}.md"}

    @_tool_errors
    def get_task(p: TaskIdParams) -> dict[str, Any]:
        return describe(todos.get_task(p.task_id))

    @_tool_errors
    def search_tasks(p: SearchTasksParams) -> dict[str, Any]:
        found = todos.list_tasks(query=p.query, status=p.status, priority=p.priority, tag=p.tag)
        return {
            "count": len(found),
            "tasks": [summarize(t) for t in found[:MAX_SEARCH_RESULTS]],
        }

    @_tool_errors
    def update_task(p: UpdateTaskParams) -> dict[str, Any]:
        given = p.model_fields_set - {"task_id"}  # only what the model actually sent
        if not given:
            raise ToolError("nothing to update: pass at least one field to change")
        changes: dict[str, Any] = {name: getattr(p, name) for name in given & _PLAIN_FIELDS}
        if given & _REMINDER_FIELDS:
            current = todos.get_task(p.task_id).reminder
            changes["reminder"] = ReminderPolicy(
                p.reminder if "reminder" in given else current.frequency,
                p.reminder_time if "reminder_time" in given else current.time_of_day,
                p.reminder_date if "reminder_date" in given else current.on_date,
                p.reminder_days if "reminder_days" in given else current.weekdays,
            )
        return {"updated": describe(todos.update_task(p.task_id, **changes))}

    @_tool_errors
    def complete_task(p: TaskIdParams) -> dict[str, Any]:
        return {"completed": describe(todos.complete_task(p.task_id))}

    for tool in [
        Tool("create_task", "Create a new task in the user's to-do list.", CreateTaskParams, create_task),
        Tool("get_task", "Get all details of one task by id.", TaskIdParams, get_task),
        Tool("search_tasks", "Find tasks by words, status, priority or tag.", SearchTasksParams, search_tasks),
        Tool("update_task", "Change fields of an existing task.", UpdateTaskParams, update_task),
        Tool("complete_task", "Mark a task as done.", TaskIdParams, complete_task),
    ]:
        registry.register(tool)


def describe(todo: Todo) -> dict[str, Any]:
    """Everything about a task, as plain JSON-friendly values (never Markdown)."""
    return {
        **summarize(todo),
        "details": todo.details,
        "reminder": describe_reminder(todo.reminder),
        "related_tasks": list(todo.related_tasks),
        "completed": todo.completed.isoformat(timespec="minutes") if todo.completed else None,
    }


def summarize(todo: Todo) -> dict[str, Any]:
    return {
        "id": todo.id,
        "title": todo.title,
        "status": str(todo.status),
        "priority": str(todo.priority),
        "deadline": todo.deadline.isoformat() if todo.deadline else None,
        "tags": list(todo.tags),
    }


def describe_reminder(policy: ReminderPolicy) -> str:
    """ReminderPolicy -> 'daily at 09:00', 'weekly on mon, thu at 18:30', 'none', ..."""
    if policy.time_of_day is None:
        return "none"
    at = f"at {policy.time_of_day:%H:%M}"
    if policy.on_date is not None:
        return f"once on {policy.on_date.isoformat()} {at}"
    if policy.weekdays:
        return f"weekly on {', '.join(policy.weekdays)} {at}"
    return f"daily {at}"


def _tool_errors(handler: Callable[[Any], Any]) -> Callable[[Any], Any]:
    """Turn task and Obsidian errors into ToolError, whose message the model sees."""

    @wraps(handler)
    def wrapper(params: Any) -> Any:
        try:
            return handler(params)
        except (TodoError, ObsidianError) as exc:
            raise ToolError(str(exc)) from exc

    return wrapper
