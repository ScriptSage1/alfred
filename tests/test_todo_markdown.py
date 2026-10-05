from datetime import date, datetime, time

import pytest

from alfred.todo import ReminderPolicy, Status, Todo, TodoFormatError, parse_todo, render_todo

FULL_TODO = Todo(
    id="finish-sih-documentation",
    title="Finish SIH documentation",
    details="Write the API section.\n\n- diagrams\n- examples",
    deadline=date(2026, 10, 9),
    priority="high",
    tags=["sih", "docs"],
    reminder=ReminderPolicy("daily", time_of_day=time(9, 0)),
    related_tasks=["sih-slides"],
    created=datetime(2026, 10, 5, 9, 30),
    updated=datetime(2026, 10, 5, 9, 30),
)

FULL_MARKDOWN = """\
---
id: "finish-sih-documentation"
title: "Finish SIH documentation"
status: "open"
priority: "high"
deadline: 2026-10-09
tags:
  - "sih"
  - "docs"
reminder: "daily"
reminder_time: "09:00"
reminder_date: null
reminder_days: []
related:
  - "[[sih-slides]]"
created: 2026-10-05T09:30:00
updated: 2026-10-05T09:30:00
completed: null
---

# Finish SIH documentation

Write the API section.

- diagrams
- examples
"""


def test_render_produces_the_exact_format():
    assert render_todo(FULL_TODO) == FULL_MARKDOWN


def test_minimal_task_still_writes_every_key():
    text = render_todo(Todo(id="buy-milk", title="Buy milk"))
    assert text == """\
---
id: "buy-milk"
title: "Buy milk"
status: "open"
priority: "medium"
deadline: null
tags: []
reminder: "none"
reminder_time: null
reminder_date: null
reminder_days: []
related: []
created: null
updated: null
completed: null
---

# Buy milk
"""


@pytest.mark.parametrize(
    "todo",
    [
        FULL_TODO,
        Todo(id="buy-milk", title="Buy milk"),
        Todo(
            id="weekly-review",
            title='Review "Q4": goals #1 & ünïcödé ✓',
            status=Status.DONE,
            reminder=ReminderPolicy("weekly", time_of_day="18:30", weekdays=["fri", "mon"]),
            completed=datetime(2026, 10, 6, 8, 0),
        ),
        Todo(
            id="call-mom",
            title="Call mom",
            reminder=ReminderPolicy("once", on_date="2026-10-07"),
            details="# A heading in the details\n\n---\n\nA line of dashes above.",
        ),
    ],
)
def test_render_then_parse_gives_back_the_same_todo(todo):
    assert parse_todo(render_todo(todo)) == todo


def test_parse_accepts_obsidian_style_edits():
    # How a file might look after editing properties in Obsidian's UI:
    # no quotes, one-item list as a plain value, no seconds, Windows line endings.
    text = (
        "---\r\n"
        "id: finish-sih-documentation\r\n"
        "title: Finish SIH documentation\r\n"
        "status: in-progress\r\n"
        "priority: high\r\n"
        "deadline: 2026-10-09\r\n"
        "tags: sih\r\n"
        "reminder: daily\r\n"
        'reminder_time: "08:15"\r\n'
        "related:\r\n"
        '  - "[[To-do/sih-slides|Slides]]"\r\n'
        "created: 2026-10-05T09:30\r\n"
        "---\r\n"
        "\r\n"
        "# I renamed the heading in Obsidian\r\n"
        "\r\n"
        "Details here.\r\n"
    )
    todo = parse_todo(text)
    assert todo.status is Status.IN_PROGRESS
    assert todo.tags == ("sih",)
    assert todo.reminder.time_of_day == time(8, 15)
    assert todo.related_tasks == ("sih-slides",)
    assert todo.created == datetime(2026, 10, 5, 9, 30)
    assert todo.title == "Finish SIH documentation"  # frontmatter wins over the heading
    assert todo.details == "Details here."


@pytest.mark.parametrize(
    "text, message",
    [
        ("# Just a note\n\nNo frontmatter.", "missing YAML frontmatter"),
        ("---\nid: [unclosed\n---\n", "invalid YAML"),
        ("---\n---\n", "key: value"),
        ('---\nid: "x"\n---\n', "missing 'title'"),
        ('---\ntitle: "X"\n---\n', "missing 'id'"),
        ('---\nid: "x"\ntitle: "X"\nstatus: "finished"\n---\n', "status must be one of"),
        ('---\nid: "x"\ntitle: "X"\nreminder: daily\nreminder_time: 9:00\n---\n', "reminder time"),
    ],
)
def test_parse_rejects_invalid_files(text, message):
    with pytest.raises(TodoFormatError, match=message):
        parse_todo(text)
