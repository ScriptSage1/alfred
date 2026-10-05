from datetime import date

import pytest

from alfred.todo import (
    Priority,
    ReminderPolicy,
    Status,
    TaskNotFoundError,
    TodoFormatError,
    TodoValidationError,
    parse_todo,
    render_todo,
)

# The `service`, `vault` and `clock` arguments are fixtures from conftest.py.


# --- create_task -----------------------------------------------------------------


def test_create_task_writes_a_markdown_file(service, vault, clock):
    todo = service.create_task(
        "Finish SIH documentation",
        deadline="2026-10-09",
        priority="high",
        tags=["sih"],
        reminder=ReminderPolicy("daily"),
    )

    assert todo.id == "finish-sih-documentation"
    assert todo.status is Status.OPEN
    assert todo.created == todo.updated == clock.now
    assert list(vault.notes) == ["To-do/finish-sih-documentation.md"]
    assert vault.notes["To-do/finish-sih-documentation.md"] == render_todo(todo)


def test_same_title_gets_a_numbered_id(service):
    ids = [service.create_task("Buy milk").id for _ in range(3)]
    assert ids == ["buy-milk", "buy-milk-2", "buy-milk-3"]


def test_invalid_input_writes_nothing(service, vault):
    with pytest.raises(TodoValidationError):
        service.create_task("Buy milk", priority="urgent")
    assert vault.notes == {}


def test_related_tasks_must_exist(service, vault):
    with pytest.raises(TodoValidationError, match="'no-such-task' does not exist"):
        service.create_task("Write report", related_tasks=["no-such-task"])
    assert vault.notes == {}


def test_create_related_to_a_task_with_the_same_title(service):
    first = service.create_task("Write report")
    second = service.create_task("Write report", related_tasks=[first.id])
    assert second.id == "write-report-2"
    assert second.related_tasks == ("write-report",)


# --- get_task --------------------------------------------------------------------


def test_get_task_reads_it_back(service):
    created = service.create_task("Buy milk", tags=["shopping"])
    assert service.get_task("buy-milk") == created


def test_get_missing_task(service):
    with pytest.raises(TaskNotFoundError):
        service.get_task("nope")


def test_get_task_refuses_ids_that_could_escape_the_folder(service, vault):
    vault.notes["secret.md"] = "do not read"
    with pytest.raises(TodoValidationError):
        service.get_task("../secret")


def test_get_task_detects_a_file_whose_id_does_not_match(service, vault):
    created = service.create_task("Buy milk")
    vault.notes["To-do/other.md"] = render_todo(created)  # says id "buy-milk"
    with pytest.raises(TodoFormatError, match="says its id is 'buy-milk'"):
        service.get_task("other")


# --- update_task -----------------------------------------------------------------


def test_update_task_changes_fields_and_timestamp(service, vault, clock):
    created = service.create_task("Buy milk")
    clock.advance(hours=1)

    updated = service.update_task("buy-milk", priority="high", tags=["shopping"], deadline="2026-10-07")

    assert updated.priority is Priority.HIGH
    assert updated.tags == ("shopping",)
    assert updated.deadline == date(2026, 10, 7)
    assert updated.created == created.created
    assert updated.updated == clock.now
    assert parse_todo(vault.notes["To-do/buy-milk.md"]) == updated


def test_renaming_a_task_keeps_its_id(service, vault):
    service.create_task("Buy milk")
    renamed = service.update_task("buy-milk", title="Buy oat milk")
    assert renamed.id == "buy-milk"
    assert list(vault.notes) == ["To-do/buy-milk.md"]


@pytest.mark.parametrize("field", ["id", "created", "completed", "colour"])
def test_update_rejects_fields_that_cannot_be_changed(service, field):
    service.create_task("Buy milk")
    with pytest.raises(TodoValidationError, match=f"cannot update {field}"):
        service.update_task("buy-milk", **{field: "x"})


def test_invalid_update_leaves_the_file_unchanged(service, vault):
    service.create_task("Buy milk")
    before = vault.notes["To-do/buy-milk.md"]
    with pytest.raises(TodoValidationError):
        service.update_task("buy-milk", deadline="someday")
    assert vault.notes["To-do/buy-milk.md"] == before


def test_update_missing_task(service):
    with pytest.raises(TaskNotFoundError):
        service.update_task("nope", priority="low")


def test_reminder_none_clears_the_reminder(service):
    service.create_task("Buy milk", reminder=ReminderPolicy("daily"))
    assert service.update_task("buy-milk", reminder=None).reminder == ReminderPolicy()


def test_newly_added_related_tasks_must_exist(service):
    service.create_task("Buy milk")
    with pytest.raises(TodoValidationError, match="does not exist"):
        service.update_task("buy-milk", related_tasks=["ghost"])


def test_existing_link_to_a_deleted_task_does_not_block_updates(service):
    service.create_task("Plan party")
    service.create_task("Buy cake", related_tasks=["plan-party"])
    service.delete_task("plan-party")
    assert service.update_task("buy-cake", priority="high").related_tasks == ("plan-party",)


# --- complete_task ---------------------------------------------------------------


def test_complete_task(service, clock):
    service.create_task("Buy milk")
    clock.advance(hours=2)
    done = service.complete_task("buy-milk")
    assert done.status is Status.DONE
    assert done.completed == clock.now


def test_completing_twice_keeps_the_first_completion_time(service, clock):
    service.create_task("Buy milk")
    first = service.complete_task("buy-milk")
    clock.advance(days=1)
    assert service.complete_task("buy-milk").completed == first.completed


def test_reopening_a_task_clears_completed(service):
    service.create_task("Buy milk")
    service.complete_task("buy-milk")
    reopened = service.update_task("buy-milk", status="open")
    assert reopened.status is Status.OPEN
    assert reopened.completed is None


# --- delete_task -----------------------------------------------------------------


def test_delete_task(service, vault):
    service.create_task("Buy milk")
    service.delete_task("buy-milk")
    assert vault.notes == {}
    with pytest.raises(TaskNotFoundError):
        service.get_task("buy-milk")


def test_delete_missing_task(service):
    with pytest.raises(TaskNotFoundError):
        service.delete_task("nope")


# --- list_tasks ------------------------------------------------------------------


def test_list_tasks_when_folder_is_empty(service):
    assert service.list_tasks() == []


def test_list_tasks_sorted_by_deadline_then_priority_then_title(service):
    service.create_task("No deadline")
    service.create_task("Later", deadline="2026-10-20")
    service.create_task("Soon low", deadline="2026-10-07", priority="low")
    service.create_task("Soon high", deadline="2026-10-07", priority="high")
    service.create_task("Another no deadline", priority="high")

    titles = [t.title for t in service.list_tasks()]
    assert titles == ["Soon high", "Soon low", "Later", "Another no deadline", "No deadline"]


def test_list_tasks_filters(service):
    service.create_task("Buy milk", tags=["shopping"], priority="low")
    service.create_task("Write report", tags=["work"], priority="high")
    service.create_task("Call bank", tags=["work"])
    service.complete_task("call-bank")

    assert [t.id for t in service.list_tasks(status="done")] == ["call-bank"]
    assert [t.id for t in service.list_tasks(priority="high")] == ["write-report"]
    assert [t.id for t in service.list_tasks(tag="#Work", status="open")] == ["write-report"]


def test_list_tasks_skips_files_that_are_not_tasks(service, vault):
    service.create_task("Buy milk")
    vault.notes["To-do/Shopping ideas.md"] = "Just a normal note."
    vault.notes["To-do/broken.md"] = "---\nid: broken\n---\n"
    vault.notes["To-do/image.png"] = "binary"
    vault.notes["To-do/archive/old.md"] = "in a subfolder"
    vault.notes["Elsewhere/task.md"] = "another folder"

    assert [t.id for t in service.list_tasks()] == ["buy-milk"]


def test_custom_folder(vault, clock):
    from alfred.todo import TodoService

    service = TodoService(vault, folder="/Projects/Tasks/", clock=clock)
    service.create_task("Buy milk")
    assert list(vault.notes) == ["Projects/Tasks/buy-milk.md"]
