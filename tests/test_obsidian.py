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

    with pytest.raises(ObsidianConnectionError, match="Is Obsidian open"):
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
