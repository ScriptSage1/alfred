"""Manual test: can Alfred manage tasks in your real Obsidian vault?

Run it from the project folder with Obsidian open:

    python scripts/check_todos.py          # creates a test task, then deletes it
    python scripts/check_todos.py --keep   # leaves the task in To-do/ so you can look at it

Deleted tasks go to Obsidian's trash, so nothing is lost permanently.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta

from alfred.config import ConfigError, load_config
from alfred.obsidian import ObsidianClient, ObsidianError
from alfred.todo import ReminderPolicy, Status, TodoError, TodoService


def step(message: str) -> None:
    print(f"  - {message}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--keep", action="store_true", help="do not delete the test task")
    args = parser.parse_args()

    try:
        settings = load_config().obsidian
    except ConfigError as exc:
        print(f"Config problem: {exc}")
        return 1

    try:
        with ObsidianClient.from_settings(settings) as obsidian:
            todos = TodoService(obsidian)
            print(f"Checks (folder: {todos.folder}/)")

            todo = todos.create_task(
                "Alfred test task",
                details="Created by scripts/check_todos.py.",
                deadline=date.today() + timedelta(days=3),
                priority="high",
                tags=["alfred", "test"],
                reminder=ReminderPolicy("daily", time_of_day="09:00"),
            )
            path = f"{todos.folder}/{todo.id}.md"
            step(f"created {path}")

            if todos.get_task(todo.id) != todo:
                print("FAILED: the task read back differently.")
                return 1
            step("read it back, every field matches")

            todos.update_task(todo.id, details="Updated by scripts/check_todos.py.", tags=["alfred"])
            step("updated details and tags")

            done = todos.complete_task(todo.id)
            if done.status is not Status.DONE or done.completed is None:
                print("FAILED: the task was not marked done.")
                return 1
            step(f"completed it at {done.completed:%H:%M:%S}")

            listed = [t.id for t in todos.list_tasks(status="done", tag="alfred")]
            if todo.id not in listed:
                print(f"FAILED: list_tasks did not include the task (got {listed}).")
                return 1
            step(f"list_tasks found it ({len(todos.list_tasks())} task(s) in {todos.folder}/)")

            print(f"\nThe file Alfred wrote ({path}):\n")
            print(obsidian.read_note(path))

            if args.keep:
                print(f"Kept {path}. Delete it from Obsidian when you're done.")
            else:
                todos.delete_task(todo.id)
                step("deleted it (moved to Obsidian's trash)")
    except (ObsidianError, TodoError) as exc:
        print(f"\nFAILED: {exc}")
        return 1

    print("\nSuccess! TodoService can manage tasks in your vault.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
