"""Errors raised by the Todo feature."""


class TodoError(Exception):
    """Base class for every Todo error."""


class TodoValidationError(TodoError, ValueError):
    """A task, or a change to one, breaks the rules in alfred.todo.model."""


class TodoFormatError(TodoError):
    """A file in the To-do folder cannot be read as a task."""


class TaskNotFoundError(TodoError):
    """There is no task with the requested id."""
