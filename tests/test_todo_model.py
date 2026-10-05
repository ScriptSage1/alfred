from dataclasses import FrozenInstanceError
from datetime import date, datetime, time

import pytest

from alfred.todo import Priority, ReminderFrequency, ReminderPolicy, Status, Todo, TodoValidationError
from alfred.todo.model import slugify


def test_minimal_todo_has_sensible_defaults():
    todo = Todo(id="buy-milk", title="Buy milk")
    assert todo.status is Status.OPEN
    assert todo.priority is Priority.MEDIUM
    assert todo.deadline is None
    assert todo.tags == ()
    assert todo.reminder == ReminderPolicy()  # no reminder
    assert todo.related_tasks == ()


def test_friendly_input_is_converted_to_canonical_values():
    todo = Todo(
        id="x",
        title="  Write report  ",
        details="\n  some details \n",
        status="In Progress",
        priority="HIGH",
        deadline="2026-10-09",
        tags=["#SIH", "Docs ", "sih", "project/alfred"],
        related_tasks=[" other-task "],
        created="2026-10-05T09:30",
    )
    assert todo.title == "Write report"
    assert todo.details == "some details"
    assert todo.status is Status.IN_PROGRESS
    assert todo.priority is Priority.HIGH
    assert todo.deadline == date(2026, 10, 9)
    assert todo.tags == ("sih", "docs", "project/alfred")  # lower-cased, '#' removed, no duplicates
    assert todo.related_tasks == ("other-task",)
    assert todo.created == datetime(2026, 10, 5, 9, 30)


@pytest.mark.parametrize(
    "fields, message",
    [
        ({"id": "Bad Id"}, "invalid task id"),
        ({"id": "../secrets"}, "invalid task id"),
        ({"id": ""}, "invalid task id"),
        ({"title": ""}, "title"),
        ({"title": "   "}, "title"),
        ({"title": "two\nlines"}, "single line"),
        ({"title": "x" * 201}, "longer than"),
        ({"status": "finished"}, "status must be one of"),
        ({"priority": "urgent"}, "priority must be one of"),
        ({"deadline": "next friday"}, "deadline must be a date"),
        ({"tags": "sih"}, "must be a list"),
        ({"tags": ["#"]}, "invalid tag"),
        ({"tags": ["2026"]}, "invalid tag"),
        ({"tags": ["a b!"]}, "invalid tag"),
        ({"related_tasks": ["Not An Id"]}, "invalid task id"),
        ({"related_tasks": ["buy-milk"]}, "related to itself"),
        ({"reminder": "daily"}, "ReminderPolicy"),
    ],
)
def test_invalid_todos_are_rejected(fields, message):
    values = {"id": "buy-milk", "title": "Buy milk", **fields}
    with pytest.raises(TodoValidationError, match=message):
        Todo(**values)


def test_todo_is_immutable():
    todo = Todo(id="buy-milk", title="Buy milk")
    with pytest.raises(FrozenInstanceError):
        todo.title = "Something else"


# --- ReminderPolicy ------------------------------------------------------------


def test_daily_reminder_defaults_to_nine_am():
    policy = ReminderPolicy("daily")
    assert policy.frequency is ReminderFrequency.DAILY
    assert policy.time_of_day == time(9, 0)


def test_reminder_time_accepts_text():
    assert ReminderPolicy("daily", time_of_day="7:45").time_of_day == time(7, 45)


def test_once_reminder_needs_a_date():
    with pytest.raises(TodoValidationError, match="needs a date"):
        ReminderPolicy("once")
    assert ReminderPolicy("once", on_date="2026-10-08").on_date == date(2026, 10, 8)


def test_weekly_reminder_needs_weekdays_and_normalises_them():
    with pytest.raises(TodoValidationError, match="at least one weekday"):
        ReminderPolicy("weekly")
    policy = ReminderPolicy("weekly", weekdays=["Thursday", "mon", "MON"])
    assert policy.weekdays == ("mon", "thu")  # short names, week order, no duplicates


def test_fields_that_do_not_apply_are_dropped():
    policy = ReminderPolicy("daily", on_date="2026-10-08", weekdays=["mon"])
    assert policy.on_date is None
    assert policy.weekdays == ()
    none = ReminderPolicy("none", time_of_day="10:00")
    assert none.time_of_day is None


@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"frequency": "hourly"}, "reminder must be one of"),
        ({"frequency": "daily", "time_of_day": "25:00"}, "reminder time"),
        ({"frequency": "daily", "time_of_day": 540}, "reminder time"),  # YAML's reading of 9:00
        ({"frequency": "weekly", "weekdays": ["funday"]}, "invalid weekday"),
        ({"frequency": "weekly", "weekdays": ["mo"]}, "invalid weekday"),
    ],
)
def test_invalid_reminders_are_rejected(kwargs, message):
    with pytest.raises(TodoValidationError, match=message):
        ReminderPolicy(**kwargs)


# --- slugify -------------------------------------------------------------------


@pytest.mark.parametrize(
    "title, expected",
    [
        ("Finish the SIH documentation!", "finish-the-sih-documentation"),
        ("Café crème  &  croissants", "cafe-creme-croissants"),
        ("2026 budget", "2026-budget"),
        ("!!!", "task"),
        ("काम", "task"),  # no Latin letters to keep
    ],
)
def test_slugify(title, expected):
    assert slugify(title) == expected


def test_slugify_cuts_long_titles_at_a_word_boundary():
    slug = slugify("word " * 30)
    assert len(slug) <= 50
    assert not slug.endswith("-")
    assert slug.endswith("word")
