"""Tests for ObsidianClient, using a fake HTTP server.

httpx.MockTransport lets us replace the network with a plain Python function
(`handler`) that receives each httpx.Request and returns an httpx.Response.
No real Obsidian is needed, and every test runs in milliseconds.
"""

import json

import httpx
import pytest

from alfred.config import ObsidianSettings
from alfred.obsidian import (
    NoteAlreadyExistsError,
    NoteNotFoundError,
    ObsidianAPIError,
    ObsidianAuthError,
    ObsidianClient,
    ObsidianConnectionError,
    ObsidianError,
    ObsidianLauncher,
    ObsidianNotRunningError,
    SearchMatch,
    SearchResult,
)

API_KEY = "test-api-key-123"
BASE_URL = "https://127.0.0.1:27124"


class FakeObsidian:
    """Records every request and answers with pre-programmed responses."""

    def __init__(self, *responses: httpx.Response) -> None:
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.responses.pop(0)


def make_client(fake) -> ObsidianClient:
    return ObsidianClient(BASE_URL, API_KEY, transport=httpx.MockTransport(fake))


# --- Every request ----------------------------------------------------------


def test_every_request_sends_the_bearer_token():
    fake = FakeObsidian(httpx.Response(200, text="# Hi"))
    make_client(fake).read_note("Inbox.md")
    assert fake.requests[0].headers["Authorization"] == f"Bearer {API_KEY}"


def test_status():
    body = {"ok": "OK", "service": "Obsidian Local REST API", "authenticated": True}
    fake = FakeObsidian(httpx.Response(200, json=body))
    assert make_client(fake).status() == body
    assert fake.requests[0].method == "GET"
    assert fake.requests[0].url.path == "/"


# --- read_note ---------------------------------------------------------------


def test_read_note():
    fake = FakeObsidian(httpx.Response(200, text="# Shopping\n- eggs\n"))
    text = make_client(fake).read_note("Lists/Shopping.md")

    request = fake.requests[0]
    assert text == "# Shopping\n- eggs\n"
    assert request.method == "GET"
    assert str(request.url) == f"{BASE_URL}/vault/Lists/Shopping.md"
    assert request.headers["Accept"] == "text/markdown"


def test_read_missing_note_raises_not_found():
    fake = FakeObsidian(httpx.Response(404, json={"errorCode": 40400, "message": "Not Found"}))
    with pytest.raises(NoteNotFoundError):
        make_client(fake).read_note("missing.md")


def test_note_paths_are_url_encoded():
    fake = FakeObsidian(httpx.Response(200, text=""))
    make_client(fake).read_note("/My Notes/Ideas #1.md")
    # leading "/" stripped; space -> %20; '#' -> %23 (otherwise it would start a URL fragment)
    assert fake.requests[0].url.raw_path == b"/vault/My%20Notes/Ideas%20%231.md"


@pytest.mark.parametrize("bad_path", ["", "   ", "folder/"])
def test_invalid_paths_are_rejected_before_any_request(bad_path):
    fake = FakeObsidian()
    with pytest.raises(ValueError):
        make_client(fake).read_note(bad_path)
    assert fake.requests == []


# --- create_note / update_note / append_note --------------------------------


def test_create_note_when_missing():
    fake = FakeObsidian(httpx.Response(404), httpx.Response(204))
    make_client(fake).create_note("New.md", "# New note")

    check, put = fake.requests
    assert check.method == "GET"
    assert put.method == "PUT"
    assert put.url.path == "/vault/New.md"
    assert put.headers["Content-Type"] == "text/markdown"
    assert put.content == b"# New note"


def test_create_note_refuses_to_overwrite():
    fake = FakeObsidian(httpx.Response(200, text="existing"))
    with pytest.raises(NoteAlreadyExistsError):
        make_client(fake).create_note("Existing.md", "new content")
    assert [r.method for r in fake.requests] == ["GET"]  # no PUT was sent


def test_update_note_replaces_content():
    fake = FakeObsidian(httpx.Response(200, text="old"), httpx.Response(204))
    make_client(fake).update_note("Note.md", "new")

    put = fake.requests[1]
    assert put.method == "PUT"
    assert put.content == b"new"


def test_update_missing_note_raises_not_found():
    fake = FakeObsidian(httpx.Response(404))
    with pytest.raises(NoteNotFoundError):
        make_client(fake).update_note("Nope.md", "new")
    assert [r.method for r in fake.requests] == ["GET"]  # no PUT was sent


def test_append_note():
    fake = FakeObsidian(httpx.Response(204))
    make_client(fake).append_note("Log.md", "\n- did a thing")

    request = fake.requests[0]
    assert request.method == "POST"
    assert request.url.path == "/vault/Log.md"
    assert request.headers["Content-Type"] == "text/markdown"
    assert request.content == b"\n- did a thing"


def test_non_ascii_content_is_sent_as_utf8():
    fake = FakeObsidian(httpx.Response(204))
    make_client(fake).append_note("Log.md", "café ☕")
    assert fake.requests[0].content == "café ☕".encode("utf-8")


def test_delete_note_moves_to_trash():
    fake = FakeObsidian(httpx.Response(204))
    make_client(fake).delete_note("To-do/old.md")

    request = fake.requests[0]
    assert request.method == "DELETE"
    assert request.url.path == "/vault/To-do/old.md"
    assert "permanent" not in request.url.params  # default: Obsidian's trash


def test_delete_missing_note():
    fake = FakeObsidian(httpx.Response(404))
    with pytest.raises(NoteNotFoundError):
        make_client(fake).delete_note("nope.md")


# --- list_notes ----------------------------------------------------------------


def test_list_notes_returns_vault_paths_of_files_only():
    fake = FakeObsidian(httpx.Response(200, json={"files": ["a.md", "archive/", "b c.md"]}))
    paths = make_client(fake).list_notes("To-do")

    assert fake.requests[0].url.path == "/vault/To-do/"
    assert paths == ["To-do/a.md", "To-do/b c.md"]


def test_list_notes_of_missing_folder_is_empty():
    fake = FakeObsidian(httpx.Response(404, json={"errorCode": 40400, "message": "Not Found"}))
    assert make_client(fake).list_notes("Nope") == []


def test_list_notes_of_vault_root():
    fake = FakeObsidian(httpx.Response(200, json={"files": ["Inbox.md", "To-do/"]}))
    assert make_client(fake).list_notes() == ["Inbox.md"]
    assert fake.requests[0].url.path == "/vault/"


# --- search_notes ------------------------------------------------------------


def test_search_notes():
    body = [
        {
            "filename": "Recipes/Pancakes.md",
            "score": -0.5,
            "matches": [{"match": {"start": 10, "end": 15}, "context": "...add the eggs..."}],
        }
    ]
    fake = FakeObsidian(httpx.Response(200, json=body))
    results = make_client(fake).search_notes("eggs")

    request = fake.requests[0]
    assert request.method == "POST"
    assert request.url.path == "/search/simple/"
    assert request.url.params["query"] == "eggs"
    assert request.url.params["contextLength"] == "100"
    assert results == [
        SearchResult(
            filename="Recipes/Pancakes.md",
            score=-0.5,
            matches=[SearchMatch(context="...add the eggs...", start=10, end=15)],
        )
    ]


def test_search_with_no_results():
    fake = FakeObsidian(httpx.Response(200, json=[]))
    assert make_client(fake).search_notes("zzz") == []


# --- Errors -------------------------------------------------------------------


def test_wrong_api_key_raises_auth_error_without_leaking_key():
    fake = FakeObsidian(httpx.Response(401, json={"errorCode": 40101, "message": "Unauthorized"}))
    with pytest.raises(ObsidianAuthError) as excinfo:
        make_client(fake).read_note("Note.md")
    assert API_KEY not in str(excinfo.value)


def test_other_errors_include_obsidian_error_code_and_message():
    body = {"errorCode": 40510, "message": "Path is a directory"}
    fake = FakeObsidian(httpx.Response(405, json=body))
    with pytest.raises(ObsidianAPIError) as excinfo:
        make_client(fake).append_note("Folder.md", "x")
    assert excinfo.value.status_code == 405
    assert excinfo.value.error_code == 40510
    assert "Path is a directory" in str(excinfo.value)


def test_error_with_non_json_body():
    fake = FakeObsidian(httpx.Response(500, text="boom"))
    with pytest.raises(ObsidianAPIError, match="boom"):
        make_client(fake).append_note("Note.md", "x")


def test_obsidian_not_running_raises_connection_error():
    def refuse(request):
        raise httpx.ConnectError("connection refused", request=request)

    with pytest.raises(ObsidianConnectionError, match="Obsidian isn't open"):
        make_client(refuse).read_note("Note.md")


def test_timeout_raises_connection_error():
    def hang(request):
        raise httpx.ReadTimeout("too slow", request=request)

    with pytest.raises(ObsidianConnectionError, match="timed out"):
        make_client(hang).read_note("Note.md")


def test_all_client_errors_share_one_base_class():
    for error in (ObsidianConnectionError, ObsidianAuthError, NoteNotFoundError,
                  NoteAlreadyExistsError, ObsidianAPIError):
        assert issubclass(error, ObsidianError)


# --- Construction & secrets ---------------------------------------------------


def test_repr_never_shows_the_api_key():
    client = make_client(FakeObsidian())
    assert API_KEY not in repr(client)


def test_empty_api_key_is_rejected():
    with pytest.raises(ObsidianError, match="API key"):
        ObsidianClient(BASE_URL, "")


def test_from_settings_requires_an_api_key():
    with pytest.raises(ObsidianError, match="OBSIDIAN_API_KEY"):
        ObsidianClient.from_settings(ObsidianSettings(api_key=None))


def test_from_settings_reports_missing_ca_cert(tmp_path):
    settings = ObsidianSettings(api_key=API_KEY, ca_cert=tmp_path / "missing.crt")
    with pytest.raises(ObsidianError, match="certificate not found"):
        ObsidianClient.from_settings(settings)


def test_from_settings_with_plain_http_ignores_ca_cert(tmp_path):
    # Plain HTTP has no TLS, so a missing certificate file must not matter.
    settings = ObsidianSettings(
        url="http://127.0.0.1:27123", api_key=API_KEY, ca_cert=tmp_path / "missing.crt"
    )
    with ObsidianClient.from_settings(settings) as client:
        assert client.base_url == "http://127.0.0.1:27123"


# --- Starting Obsidian automatically ---------------------------------------------


class ClosedObsidian:
    """Refuses connections until it is launched; then it takes a few polls to start."""

    def __init__(self, polls_to_start: int = 2) -> None:
        self.opened_uris: list[str] = []
        self.polls_to_start = polls_to_start
        self.running = False

    def open_uri(self, uri: str) -> None:
        self.opened_uris.append(uri)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if not self.running and self.opened_uris:
            self.polls_to_start -= 1
            self.running = self.polls_to_start < 0
        if not self.running:
            raise httpx.ConnectError("connection refused", request=request)
        return httpx.Response(200, text="# Shopping")


class FakeTime:
    """sleep() moves the clock forward instantly, so tests never really wait."""

    def __init__(self) -> None:
        self.now = 0.0

    def sleep(self, seconds: float) -> None:
        self.now += seconds

    def clock(self) -> float:
        return self.now


def client_with_launcher(obsidian: ClosedObsidian, timeout: float = 30) -> ObsidianClient:
    fake_time = FakeTime()
    launcher = ObsidianLauncher(
        "alfred", timeout=timeout, open_uri=obsidian.open_uri, sleep=fake_time.sleep, clock=fake_time.clock
    )
    return ObsidianClient(BASE_URL, API_KEY, transport=httpx.MockTransport(obsidian), launcher=launcher)


def test_obsidian_is_started_and_the_request_retried():
    obsidian = ClosedObsidian()
    assert client_with_launcher(obsidian).read_note("Lists/Shopping.md") == "# Shopping"
    assert obsidian.opened_uris == ["obsidian://open?vault=alfred"]


def test_gives_up_if_the_plugin_never_answers():
    obsidian = ClosedObsidian(polls_to_start=10_000)
    with pytest.raises(ObsidianNotRunningError, match="did not answer within 5 seconds"):
        client_with_launcher(obsidian, timeout=5).read_note("x.md")
    assert len(obsidian.opened_uris) == 1  # tried once, did not keep relaunching


def test_without_a_launcher_nothing_is_started():
    obsidian = ClosedObsidian()
    client = ObsidianClient(BASE_URL, API_KEY, transport=httpx.MockTransport(obsidian))
    with pytest.raises(ObsidianNotRunningError, match="Obsidian isn't open"):
        client.read_note("x.md")
    assert obsidian.opened_uris == []


def test_certificate_problems_do_not_start_obsidian():
    opened = []

    def bad_certificate(request):
        raise httpx.ConnectError("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed", request=request)

    launcher = ObsidianLauncher("alfred", open_uri=opened.append)
    client = ObsidianClient(BASE_URL, API_KEY, transport=httpx.MockTransport(bad_certificate), launcher=launcher)
    with pytest.raises(ObsidianConnectionError, match="certificate") as excinfo:
        client.read_note("x.md")
    assert not isinstance(excinfo.value, ObsidianNotRunningError)
    assert opened == []


@pytest.mark.parametrize(
    "vault, uri",
    [("alfred", "obsidian://open?vault=alfred"), ("My Vault", "obsidian://open?vault=My%20Vault"), (None, "obsidian://open")],
)
def test_launch_uri(vault, uri):
    assert ObsidianLauncher(vault).uri == uri


def test_from_settings_respects_auto_launch():
    on = ObsidianClient.from_settings(ObsidianSettings(url="http://x:1", api_key=API_KEY, vault="alfred"))
    off = ObsidianClient.from_settings(ObsidianSettings(url="http://x:1", api_key=API_KEY, auto_launch=False))
    assert on._launcher is not None and on._launcher.vault == "alfred"
    assert off._launcher is None
