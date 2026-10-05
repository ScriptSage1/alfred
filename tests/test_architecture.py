"""Rules about how Alfred's code is organised, checked automatically.

Each rule protects a boundary: if someone (human or AI) puts code in the
wrong place, a test fails and says where.
"""

import ast
from pathlib import Path

import alfred

SRC = Path(alfred.__file__).parent


def imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def files_importing(prefixes: tuple[str, ...], *, within: Path = SRC) -> set[str]:
    found = set()
    for path in within.rglob("*.py"):
        if any(m == p or m.startswith(p + ".") for m in imported_modules(path) for p in prefixes):
            found.add(path.relative_to(SRC).as_posix())
    return found


def test_only_obsidian_client_does_http():
    http = ("httpx", "requests", "urllib.request", "http.client", "aiohttp")
    assert files_importing(http) == {"obsidian.py"}


def test_only_the_copilot_backend_imports_the_copilot_sdk():
    assert files_importing(("copilot",)) == {"agent/copilot_backend.py"}


def test_only_the_ui_imports_qt():
    assert {f.split("/")[0] for f in files_importing(("PySide6",))} == {"ui"}


def test_the_agent_and_registry_know_nothing_about_tasks_or_obsidian():
    for package in ("agent", "tools"):
        assert files_importing(("alfred.todo", "alfred.obsidian"), within=SRC / package) == set()


def test_the_ui_knows_nothing_about_tasks_obsidian_or_copilot():
    forbidden = ("alfred.todo", "alfred.obsidian", "alfred.assistant", "alfred.agent.copilot_backend", "copilot")
    assert files_importing(forbidden, within=SRC / "ui") == set()
