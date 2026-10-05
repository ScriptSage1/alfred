"""TodoService: everything Alfred can do with tasks.

This is the only place that knows tasks are Markdown files in a vault folder.
It turns a call like complete_task("buy-milk") into "read To-do/buy-milk.md,
change it, render it, write it back".
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Iterator
from dataclasses import replace
from datetime import date, datetime
from pathlib import PurePosixPath
from typing import Any, Protocol

from alfred.obsidian import NoteAlreadyExistsError, NoteNotFoundError
from alfred.todo.errors import TaskNotFoundError, TodoError, TodoFormatError, TodoValidationError
from alfred.todo.markdown import parse_todo, render_todo
from alfred.todo.model import (
    Priority,
    ReminderPolicy,
    Status,
    Todo,
    check_task_id,
    normalize_tag,
    normalize_task_ids,
    slugify,
    to_enum,
)

log = logging.getLogger(__name__)

DEFAULT_FOLDER = "To-do"
UPDATABLE_FIELDS = frozenset(
    {"title", "details", "deadline", "status", "priority", "tags", "reminder", "related_tasks"}
)
_PRIORITY_ORDER = {Priority.HIGH: 0, Priority.MEDIUM: 1, Priority.LOW: 2}
_MAX_ID_ATTEMPTS = 100


class NoteStore(Protocol):
    """The note operations TodoService needs. ObsidianClient has all of them;
    tests use an in-memory stand-in with the same methods."""

    def read_note(self, path: str) -> str: ...
    def create_note(self, path: str, content: str) -> None: ...
    def update_note(self, path: str, content: str) -> None: ...
    def delete_note(self, path: str) -> None: ...
    def list_notes(self, folder: str) -> list[str]: ...


def _now() -> datetime:
    return datetime.now().replace(microsecond=0)


class TodoService:
    def __init__(
        self,
        notes: NoteStore,
        *,
        folder: str = DEFAULT_FOLDER,
        clock: Callable[[], datetime] = _now,
    ) -> None:
        self._notes = notes
        self.folder = folder.strip("/")
        self._clock = clock  # tests pass a fake clock to control timestamps

    # --- Commands -------------------------------------------------------------

    def create_task(
        self,
        title: str,
        *,
        details: str = "",
        deadline: date | str | None = None,
        priority: Priority | str = Priority.MEDIUM,
        tags: Iterable[str] = (),
        reminder: ReminderPolicy | None = None,
        related_tasks: Iterable[str] = (),
    ) -> Todo:
        """Create and save a new open task. Returns it, including its new id."""
        related = normalize_task_ids(related_tasks)
        self._require_existing(related)
        now = self._clock()

        base_id = slugify(title) if isinstance(title, str) else "task"
        for task_id in _candidate_ids(base_id):
            if task_id in related:
                continue  # that id belongs to an existing task
            todo = Todo(  # validates everything before anything is written
                id=task_id,
                title=title,
                details=details,
                deadline=deadline,
                priority=priority,
                tags=tags,
                reminder=reminder or ReminderPolicy(),
                related_tasks=related,
                created=now,
                updated=now,
            )
            try:
                self._notes.create_note(self._path(task_id), render_todo(todo))
            except NoteAlreadyExistsError:
                continue  # another task has this id; try the next one
            log.info("created task %r", task_id)
            return todo
        raise TodoError(f"could not find a free id for {title!r}")

    def get_task(self, task_id: str) -> Todo:
        task_id = check_task_id(task_id)  # also blocks paths like "../secret"
        try:
            text = self._notes.read_note(self._path(task_id))
        except NoteNotFoundError:
            raise TaskNotFoundError(f"no task with id {task_id!r}") from None
        todo = parse_todo(text)
        if todo.id != task_id:
            raise TodoFormatError(f"{self._path(task_id)} says its id is {todo.id!r}")
        return todo

    def update_task(self, task_id: str, **changes: Any) -> Todo:
        """Change some fields, e.g. update_task("buy-milk", priority="high")."""
        unknown = set(changes) - UPDATABLE_FIELDS
        if unknown:
            raise TodoValidationError(
                f"cannot update {', '.join(sorted(unknown))}; "
                f"allowed fields: {', '.join(sorted(UPDATABLE_FIELDS))}"
            )
        current = self.get_task(task_id)

        if "related_tasks" in changes:
            related = normalize_task_ids(changes["related_tasks"])
            self._require_existing([r for r in related if r not in current.related_tasks])
            changes["related_tasks"] = related
        if "reminder" in changes and changes["reminder"] is None:
            changes["reminder"] = ReminderPolicy()  # None means "no reminder"

        now = self._clock()
        status = to_enum(Status, changes.get("status", current.status), "status")
        if status is not Status.DONE:
            completed = None
        elif current.status is Status.DONE:
            completed = current.completed  # already done: keep the original time
        else:
            completed = now

        updated = replace(current, **changes, updated=now, completed=completed)
        self._notes.update_note(self._path(current.id), render_todo(updated))
        log.info("updated task %r (%s)", current.id, ", ".join(sorted(changes)))
        return updated

    def complete_task(self, task_id: str) -> Todo:
        return self.update_task(task_id, status=Status.DONE)

    def delete_task(self, task_id: str) -> None:
        """Delete a task. Obsidian moves the file to its trash, so it can be restored."""
        task_id = check_task_id(task_id)
        try:
            self._notes.delete_note(self._path(task_id))
        except NoteNotFoundError:
            raise TaskNotFoundError(f"no task with id {task_id!r}") from None
        log.info("deleted task %r", task_id)

    # --- Queries --------------------------------------------------------------

    def list_tasks(
        self,
        *,
        query: str | None = None,
        status: Status | str | None = None,
        priority: Priority | str | None = None,
        tag: str | None = None,
    ) -> list[Todo]:
        """All tasks matching the filters, soonest deadline first, then by priority.

        ``query`` keeps tasks whose id, title, details or tags contain every word.
        """
        want_status = to_enum(Status, status, "status") if status is not None else None
        want_priority = to_enum(Priority, priority, "priority") if priority is not None else None
        want_tag = normalize_tag(tag) if tag is not None else None
        words = query.casefold().split() if query else []

        tasks = []
        for path in self._notes.list_notes(self.folder):
            note = PurePosixPath(path)
            if note.suffix != ".md":
                continue
            try:
                todo = self.get_task(note.stem)
            except (TodoFormatError, TodoValidationError) as exc:
                log.warning("skipping %s: not a valid task (%s)", path, exc)
                continue
            except TaskNotFoundError:
                continue  # deleted while we were listing
            if want_status is not None and todo.status is not want_status:
                continue
            if want_priority is not None and todo.priority is not want_priority:
                continue
            if want_tag is not None and want_tag not in todo.tags:
                continue
            if words and not _matches(todo, words):
                continue
            tasks.append(todo)
        return sorted(tasks, key=_sort_key)

    # --- Internals ------------------------------------------------------------

    def _path(self, task_id: str) -> str:
        return f"{self.folder}/{task_id}.md" if self.folder else f"{task_id}.md"

    def _require_existing(self, task_ids: Iterable[str]) -> None:
        for task_id in task_ids:
            try:
                self._notes.read_note(self._path(task_id))
            except NoteNotFoundError:
                raise TodoValidationError(f"related task {task_id!r} does not exist") from None


def _candidate_ids(base_id: str) -> Iterator[str]:
    yield base_id
    for n in range(2, _MAX_ID_ATTEMPTS + 1):
        yield f"{base_id}-{n}"


def _matches(todo: Todo, words: list[str]) -> bool:
    text = " ".join([todo.id, todo.title, todo.details, *todo.tags]).casefold()
    return all(word in text for word in words)


def _sort_key(todo: Todo) -> tuple:
    return (
        todo.deadline is None,            # tasks with a deadline first
        todo.deadline or date.max,        # earliest deadline first
        _PRIORITY_ORDER[todo.priority],   # then high before low
        todo.title.casefold(),            # then alphabetical
    )
