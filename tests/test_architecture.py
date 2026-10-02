"""Rules about how Alfred's code is organised, checked automatically."""

import ast
from pathlib import Path

import alfred

SRC = Path(alfred.__file__).parent
HTTP_LIBRARIES = {"httpx", "requests", "urllib.request", "http.client", "aiohttp"}
ALLOWED = {SRC / "obsidian.py"}


def imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_only_obsidian_client_does_http():
    offenders = [
        str(path.relative_to(SRC))
        for path in SRC.rglob("*.py")
        if path not in ALLOWED and imported_modules(path) & HTTP_LIBRARIES
    ]
    assert offenders == [], f"HTTP code must live in obsidian.py, found in: {offenders}"
