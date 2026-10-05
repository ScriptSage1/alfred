"""The Todo data model: what a task *is*, independent of where it is stored.

Every rule about valid tasks lives here, in the __post_init__ methods. Anything
that creates a Todo (the service, the Markdown parser, later an AI tool call)
goes through the same checks, so an invalid task cannot exist in memory.

The models accept friendly input ("high", "2026-10-09", ["#SIH"]) and convert
it to one canonical form (Priority.HIGH, date(2026, 10, 9), ("sih",)).
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, time
from enum import StrEnum
from typing import Any, TypeVar

from alfred.todo.errors import TodoValidationError


class Status(StrEnum):
    OPEN = "open"
    IN_PROGRESS = "in-progress"
    DONE = "done"
    CANCELLED = "cancelled"


class Priority(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ReminderFrequency(StrEnum):
    NONE = "none"      # no reminders
    ONCE = "once"      # a single reminder on `on_date`
    DAILY = "daily"    # every day until the task is done
    WEEKLY = "weekly"  # on each of `weekdays` until the task is done


WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_WEEKDAY_NAMES = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
DEFAULT_REMINDER_TIME = time(9, 0)
MAX_TITLE_LENGTH = 200
MAX_ID_LENGTH = 80

_TASK_ID = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_TAG = re.compile(r"[\w/-]+")  # Obsidian tags: letters, digits, _, -, /
_TIME = re.compile(r"(\d{1,2}):(\d{2})")


@dataclass(frozen=True)
class ReminderPolicy:
    """When Alfred should remind you about a task. (Sending reminders comes later.)

    Fields that don't apply to the frequency are dropped, e.g. a DAILY
    reminder ignores `weekdays`.
    """

    frequency: ReminderFrequency = ReminderFrequency.NONE
    time_of_day: time | None = None  # defaults to 09:00 for any real reminder
    on_date: date | None = None      # ONCE only
    weekdays: tuple[str, ...] = ()   # WEEKLY only, e.g. ("mon", "thu")

    def __post_init__(self) -> None:
        frequency = to_enum(ReminderFrequency, self.frequency, "reminder")
        _set(self, "frequency", frequency)

        time_of_day = None
        if frequency is not ReminderFrequency.NONE:
            time_of_day = _to_time(self.time_of_day, "reminder time") or DEFAULT_REMINDER_TIME
        _set(self, "time_of_day", time_of_day)

        on_date = None
        if frequency is ReminderFrequency.ONCE:
            on_date = _to_date(self.on_date, "reminder date")
            if on_date is None:
                raise TodoValidationError("a 'once' reminder needs a date")
        _set(self, "on_date", on_date)

        weekdays: tuple[str, ...] = ()
        if frequency is ReminderFrequency.WEEKLY:
            days = {_weekday(d) for d in _as_list(self.weekdays, "reminder days")}
            weekdays = tuple(sorted(days, key=WEEKDAYS.index))
            if not weekdays:
                raise TodoValidationError("a 'weekly' reminder needs at least one weekday")
        _set(self, "weekdays", weekdays)


@dataclass(frozen=True)
class Todo:
    id: str
    title: str
    details: str = ""
    deadline: date | None = None
    status: Status = Status.OPEN
    priority: Priority = Priority.MEDIUM
    tags: tuple[str, ...] = ()
    reminder: ReminderPolicy = field(default_factory=ReminderPolicy)
    related_tasks: tuple[str, ...] = ()  # ids of other tasks
    created: datetime | None = None
    updated: datetime | None = None
    completed: datetime | None = None

    def __post_init__(self) -> None:
        _set(self, "id", check_task_id(self.id))
        _set(self, "title", _title(self.title))
        if not isinstance(self.details, str):
            raise TodoValidationError("details must be text")
        _set(self, "details", self.details.strip())
        _set(self, "deadline", _to_date(self.deadline, "deadline"))
        _set(self, "status", to_enum(Status, self.status, "status"))
        _set(self, "priority", to_enum(Priority, self.priority, "priority"))
        _set(self, "tags", _unique(normalize_tag(t) for t in _as_list(self.tags, "tags")))
        if not isinstance(self.reminder, ReminderPolicy):
            raise TodoValidationError("reminder must be a ReminderPolicy")

        related = normalize_task_ids(self.related_tasks)
        if self.id in related:
            raise TodoValidationError("a task cannot be related to itself")
        _set(self, "related_tasks", related)

        for name in ("created", "updated", "completed"):
            _set(self, name, _to_datetime(getattr(self, name), name))


# --- Public helpers -----------------------------------------------------------

E = TypeVar("E", bound=StrEnum)


def to_enum(enum_type: type[E], value: Any, name: str) -> E:
    """Convert "High" / "in progress" / Priority.HIGH to the enum member."""
    if isinstance(value, str):
        value = value.strip().lower().replace("_", "-").replace(" ", "-")
    try:
        return enum_type(value)
    except ValueError:
        allowed = ", ".join(member.value for member in enum_type)
        raise TodoValidationError(f"{name} must be one of: {allowed} (got {value!r})") from None


def check_task_id(value: Any) -> str:
    """Task ids are also file names, so they are kept strictly to a-z, 0-9 and '-'."""
    if not isinstance(value, str) or len(value) > MAX_ID_LENGTH or not _TASK_ID.fullmatch(value):
        raise TodoValidationError(
            f"invalid task id {value!r}: use lowercase letters, digits and single hyphens"
        )
    return value


def normalize_task_ids(values: Any) -> tuple[str, ...]:
    ids = _as_list(values, "related tasks")
    return _unique(check_task_id(v.strip() if isinstance(v, str) else v) for v in ids)


def normalize_tag(value: Any) -> str:
    """'#SIH Docs' -> 'sih-docs'. Obsidian tags have no spaces and are case-insensitive."""
    if not isinstance(value, str):
        raise TodoValidationError(f"tags must be text (got {value!r})")
    tag = value.strip().lstrip("#").strip().lower().replace(" ", "-")
    if not tag or not _TAG.fullmatch(tag) or tag.isdigit():
        raise TodoValidationError(
            f"invalid tag {value!r}: use letters, digits, '-', '_' or '/', not only digits"
        )
    return tag


def slugify(text: str, max_length: int = 50) -> str:
    """Turn a title into an id: 'Finish the SIH docs!' -> 'finish-the-sih-docs'."""
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")
    if len(slug) > max_length:
        slug = slug[:max_length].rsplit("-", 1)[0]  # cut at a word boundary
    return slug.strip("-") or "task"


# --- Private helpers ----------------------------------------------------------


def _set(obj: object, name: str, value: Any) -> None:
    # Frozen dataclasses block `self.x = ...`. object.__setattr__ is the standard
    # escape hatch, used only inside __post_init__ while the object is being built.
    object.__setattr__(obj, name, value)


def _unique(items: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(items))  # removes duplicates, keeps the order


def _as_list(value: Any, name: str) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, str):
        raise TodoValidationError(f"{name} must be a list, not a single string ({value!r})")
    try:
        return list(value)
    except TypeError:
        raise TodoValidationError(f"{name} must be a list (got {value!r})") from None


def _title(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TodoValidationError("title must be non-empty text")
    title = value.strip()
    if not title.isprintable():
        raise TodoValidationError("title must be a single line without control characters")
    if len(title) > MAX_TITLE_LENGTH:
        raise TodoValidationError(f"title is longer than {MAX_TITLE_LENGTH} characters")
    return title


def _weekday(value: Any) -> str:
    text = value.strip().lower() if isinstance(value, str) else ""
    for short, full in zip(WEEKDAYS, _WEEKDAY_NAMES):
        if len(text) >= 3 and full.startswith(text):
            return short
    raise TodoValidationError(f"invalid weekday {value!r}: use mon, tue, wed, thu, fri, sat, sun")


def _to_date(value: Any, name: str) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):  # check first: a datetime is also a date
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value.strip())
        except ValueError:
            pass
    raise TodoValidationError(f"{name} must be a date like 2026-10-09 (got {value!r})")


def _to_time(value: Any, name: str) -> time | None:
    if value is None or value == "":
        return None
    if isinstance(value, time):
        return value.replace(second=0, microsecond=0)
    if isinstance(value, str) and (match := _TIME.fullmatch(value.strip())):
        try:
            return time(int(match[1]), int(match[2]))
        except ValueError:
            pass
    raise TodoValidationError(f'{name} must look like "09:00" (got {value!r})')


def _to_datetime(value: Any, name: str) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.replace(microsecond=0)
    if isinstance(value, date):
        return datetime.combine(value, time())
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.strip()).replace(microsecond=0)
        except ValueError:
            pass
    raise TodoValidationError(f"{name} must be a date and time like 2026-10-05T09:30 (got {value!r})")
