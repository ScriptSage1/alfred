"""Convert a Todo to Markdown and back.

This module alone decides what a task file looks like. render_todo() writes
every field, every time, in the same order, so all task files share one format:

    ---
    id: "finish-sih-documentation"
    title: "Finish SIH documentation"
    status: "open"
    priority: "high"
    deadline: 2026-10-09
    tags:
      - "sih"
    reminder: "daily"
    reminder_time: "09:00"
    reminder_date: null
    reminder_days: []
    related: []
    created: 2026-10-05T09:30:00
    updated: 2026-10-05T09:30:00
    completed: null
    ---

    # Finish SIH documentation

    Free-form details in Markdown.

The frontmatter is the source of truth. The "# Title" heading is regenerated
from it on every save, and everything after the heading is the details.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, time
from typing import Any

import yaml

from alfred.todo.errors import TodoFormatError, TodoValidationError
from alfred.todo.model import ReminderPolicy, Todo

_FRONTMATTER = re.compile(r"\A---[ \t]*\n(.*?)^---[ \t]*$\n?(.*)\Z", re.DOTALL | re.MULTILINE)


# --- Writing ------------------------------------------------------------------


def render_todo(todo: Todo) -> str:
    reminder = todo.reminder
    lines = [
        "---",
        f"id: {_text(todo.id)}",
        f"title: {_text(todo.title)}",
        f"status: {_text(todo.status)}",
        f"priority: {_text(todo.priority)}",
        f"deadline: {_date(todo.deadline)}",
        *_list("tags", todo.tags),
        f"reminder: {_text(reminder.frequency)}",
        f"reminder_time: {_time(reminder.time_of_day)}",
        f"reminder_date: {_date(reminder.on_date)}",
        *_list("reminder_days", reminder.weekdays),
        # [[wikilinks]] make related tasks show up as links in Obsidian's graph.
        *_list("related", [f"[[{task_id}]]" for task_id in todo.related_tasks]),
        f"created: {_datetime(todo.created)}",
        f"updated: {_datetime(todo.updated)}",
        f"completed: {_datetime(todo.completed)}",
        "---",
        "",
        f"# {todo.title}",
    ]
    if todo.details:
        lines += ["", todo.details]
    return "\n".join(lines) + "\n"


def _text(value: str) -> str:
    # A JSON string is also a valid YAML double-quoted string, and quoting
    # every string means YAML never guesses a different type (like 9:00 -> 540).
    return json.dumps(str(value), ensure_ascii=False)


def _date(value: date | None) -> str:
    return value.isoformat() if value else "null"


def _time(value: time | None) -> str:
    return _text(value.strftime("%H:%M")) if value else "null"


def _datetime(value: datetime | None) -> str:
    return value.isoformat(timespec="seconds") if value else "null"


def _list(key: str, items: Any) -> list[str]:
    if not items:
        return [f"{key}: []"]
    return [f"{key}:", *(f"  - {_text(item)}" for item in items)]


# --- Reading ------------------------------------------------------------------


def parse_todo(text: str) -> Todo:
    """Read a task file. Accepts our own format and the way Obsidian rewrites
    properties when you edit them by hand (unquoted values, missing seconds, ...)."""
    frontmatter, body = _split(text)
    try:
        data = yaml.safe_load(frontmatter)
    except yaml.YAMLError as exc:
        raise TodoFormatError(f"invalid YAML frontmatter: {exc}") from exc
    if not isinstance(data, dict):
        raise TodoFormatError("frontmatter must contain 'key: value' pairs")

    for key in ("id", "title"):
        if not data.get(key):
            raise TodoFormatError(f"frontmatter is missing {key!r}")

    try:
        return Todo(
            id=data["id"],
            title=data["title"],
            details=_details(body),
            deadline=data.get("deadline"),
            status=data.get("status") or "open",
            priority=data.get("priority") or "medium",
            tags=_as_list(data.get("tags")),
            reminder=ReminderPolicy(
                frequency=data.get("reminder") or "none",
                time_of_day=data.get("reminder_time"),
                on_date=data.get("reminder_date"),
                weekdays=_as_list(data.get("reminder_days")),
            ),
            related_tasks=[_unlink(item) for item in _as_list(data.get("related"))],
            created=data.get("created"),
            updated=data.get("updated"),
            completed=data.get("completed"),
        )
    except TodoValidationError as exc:
        raise TodoFormatError(f"invalid task: {exc}") from exc


def _split(text: str) -> tuple[str, str]:
    text = text.lstrip("﻿").replace("\r\n", "\n")
    match = _FRONTMATTER.match(text)
    if not match:
        raise TodoFormatError("missing YAML frontmatter (a task file must start with ---)")
    return match[1], match[2]


def _details(body: str) -> str:
    lines = body.strip().split("\n")
    if lines and lines[0].startswith("# "):
        lines = lines[1:]  # the title heading; the frontmatter title wins
    return "\n".join(lines).strip()


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]  # Obsidian may write a one-item list as a plain value


def _unlink(item: Any) -> Any:
    """'[[To-do/sih-slides|Slides]]' -> 'sih-slides'."""
    if isinstance(item, str) and item.startswith("[[") and item.endswith("]]"):
        target = item[2:-2].split("|")[0]
        return target.rsplit("/", 1)[-1].removesuffix(".md")
    return item
