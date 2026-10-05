"""TodoService wired to the REAL ObsidianClient, with only the network faked.

The unit tests use InMemoryVault. This test proves the two real classes work
together: TodoService -> ObsidianClient -> HTTP requests -> (fake) Obsidian.
"""

import httpx

from alfred.obsidian import ObsidianClient
from alfred.todo import Status, TodoService

API_KEY = "test-key"


class FakeObsidianServer:
    """Speaks just enough of the Local REST API to store files in a dict."""

    def __init__(self) -> None:
        self.files: dict[str, str] = {}
        self.log: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == f"Bearer {API_KEY}"
        self.log.append(f"{request.method} {request.url.path}")
        path = request.url.path.removeprefix("/vault/")  # httpx decodes %20 etc. for us

        if request.method == "GET" and (path == "" or path.endswith("/")):
            return self._list(path)
        if request.method == "GET":
            if path not in self.files:
                return httpx.Response(404, json={"errorCode": 40400, "message": "Not Found"})
            return httpx.Response(200, text=self.files[path])
        if request.method == "PUT":
            self.files[path] = request.content.decode("utf-8")
            return httpx.Response(204)
        if request.method == "DELETE":
            if self.files.pop(path, None) is None:
                return httpx.Response(404)
            return httpx.Response(204)
        return httpx.Response(405)

    def _list(self, folder: str) -> httpx.Response:
        entries = set()
        for path in self.files:
            if path.startswith(folder):
                rest = path[len(folder):]
                entries.add(rest.split("/")[0] + "/" if "/" in rest else rest)
        if not entries:
            return httpx.Response(404)
        return httpx.Response(200, json={"files": sorted(entries)})


def test_todo_service_through_obsidian_client():
    server = FakeObsidianServer()
    obsidian = ObsidianClient("https://127.0.0.1:27124", API_KEY, transport=httpx.MockTransport(server))
    todos = TodoService(obsidian)

    todo = todos.create_task("Finish SIH documentation", deadline="2026-10-09", tags=["sih"])
    assert list(server.files) == ["To-do/finish-sih-documentation.md"]
    assert server.files["To-do/finish-sih-documentation.md"].startswith('---\nid: "finish-sih-documentation"\n')

    assert todos.get_task(todo.id) == todo
    assert todos.complete_task(todo.id).status is Status.DONE
    assert [t.id for t in todos.list_tasks(status="done")] == [todo.id]

    todos.delete_task(todo.id)
    assert server.files == {}
    assert todos.list_tasks() == []

    # The actual HTTP conversation for create_task:
    assert server.log[:2] == [
        "GET /vault/To-do/finish-sih-documentation.md",  # does it exist already?
        "PUT /vault/To-do/finish-sih-documentation.md",  # no, so write it
    ]
