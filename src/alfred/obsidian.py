"""Talk to Obsidian through the Local REST API plugin.

This is the ONLY file in Alfred that makes HTTP requests. Everything else calls
ObsidianClient methods and gets back plain Python values (str, list, dataclasses)
or one of the exceptions defined below.

API reference: https://coddingtonbear.github.io/obsidian-local-rest-api/
"""

from __future__ import annotations

import logging
import os
import ssl
import sys
import threading
import time
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx

from alfred.config import ObsidianSettings

log = logging.getLogger(__name__)

MARKDOWN = "text/markdown"


# --- Errors -----------------------------------------------------------------


class ObsidianError(Exception):
    """Base class for every error raised by ObsidianClient."""


class ObsidianConnectionError(ObsidianError):
    """Obsidian could not be reached at all (not running, wrong URL, TLS problem)."""


class ObsidianNotRunningError(ObsidianConnectionError):
    """Nothing is listening at the URL: Obsidian (or its REST plugin) is not running."""


class ObsidianAuthError(ObsidianError):
    """Obsidian rejected the API key (HTTP 401 or 403)."""


class NoteNotFoundError(ObsidianError):
    """The note does not exist (HTTP 404)."""


class NoteAlreadyExistsError(ObsidianError):
    """create_note() was called for a note that already exists."""


class ObsidianAPIError(ObsidianError):
    """Any other error response from the plugin."""

    def __init__(self, status_code: int, message: str, error_code: int | None = None) -> None:
        super().__init__(f"Obsidian returned HTTP {status_code}: {message}")
        self.status_code = status_code
        self.error_code = error_code


# --- Starting Obsidian ------------------------------------------------------


class ObsidianLauncher:
    """Starts the Obsidian app and waits until its Local REST API answers.

    It opens an obsidian://open?vault=... link, which Windows hands to Obsidian
    (the installer registers that link type), the same as clicking such a link.
    """

    def __init__(
        self,
        vault: str | None = None,
        *,
        timeout: float = 30.0,
        open_uri: Callable[[str], Any] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.vault = vault
        self.timeout = timeout
        self._open_uri = open_uri or _open_uri
        self._sleep = sleep
        self._clock = clock

    @property
    def uri(self) -> str:
        return "obsidian://open" + (f"?vault={quote(self.vault)}" if self.vault else "")

    def start_and_wait(self, is_ready: Callable[[], bool]) -> None:
        log.info("Obsidian is not running; starting it (%s)", self.uri)
        self._open_uri(self.uri)
        deadline = self._clock() + self.timeout
        while self._clock() < deadline:
            self._sleep(0.5)
            if is_ready():
                log.info("Obsidian is ready")
                return
        vault = f"the '{self.vault}' vault" if self.vault else "your vault"
        raise ObsidianNotRunningError(
            f"started Obsidian, but its Local REST API did not answer within {self.timeout:.0f} "
            f"seconds. Check that the Local REST API plugin is enabled in {vault}."
        )


def _open_uri(uri: str) -> None:
    if sys.platform == "win32":
        os.startfile(uri)  # like double-clicking the link
    else:
        webbrowser.open(uri)


# --- Search results ---------------------------------------------------------


@dataclass(frozen=True)
class SearchMatch:
    context: str  # the text around the match
    start: int    # character offsets of the match within the note
    end: int


@dataclass(frozen=True)
class SearchResult:
    filename: str
    score: float
    matches: list[SearchMatch]

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> SearchResult:
        return cls(
            filename=data["filename"],
            score=data.get("score", 0.0),
            matches=[
                SearchMatch(context=m["context"], start=m["match"]["start"], end=m["match"]["end"])
                for m in data.get("matches", [])
            ],
        )


# --- The client -------------------------------------------------------------


class ObsidianClient:
    """A small, typed wrapper around the Obsidian Local REST API.

    Use it as a context manager so the underlying connection is closed::

        with ObsidianClient.from_settings(config.obsidian) as obsidian:
            text = obsidian.read_note("Inbox.md")
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        verify: ssl.SSLContext | bool = True,
        timeout: float = 10.0,
        transport: httpx.BaseTransport | None = None,
        launcher: ObsidianLauncher | None = None,
    ) -> None:
        if not api_key:
            raise ObsidianError("an Obsidian API key is required")
        self.base_url = base_url
        self._launcher = launcher  # None: never start Obsidian, just report the error
        self._launch_lock = threading.Lock()
        self._http = httpx.Client(
            base_url=base_url,
            headers={"Authorization": f"Bearer {api_key}"},
            verify=verify,
            timeout=timeout,
            transport=transport,  # tests pass a fake transport here
        )

    @classmethod
    def from_settings(cls, settings: ObsidianSettings) -> ObsidianClient:
        """Build a client from Alfred's configuration."""
        if not settings.api_key:
            raise ObsidianError(
                "no Obsidian API key configured; set the OBSIDIAN_API_KEY environment variable"
            )
        verify: ssl.SSLContext | bool = True
        if settings.ca_cert is not None and settings.url.startswith("https://"):
            if not settings.ca_cert.exists():
                raise ObsidianError(f"Obsidian CA certificate not found: {settings.ca_cert}")
            verify = ssl.create_default_context(cafile=str(settings.ca_cert))
        launcher = None
        if settings.auto_launch:
            launcher = ObsidianLauncher(settings.vault, timeout=settings.launch_timeout)
        return cls(
            settings.url, settings.api_key, verify=verify, timeout=settings.timeout, launcher=launcher
        )

    def __repr__(self) -> str:
        return f"ObsidianClient(base_url={self.base_url!r})"  # never show the key

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> ObsidianClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # --- Public API ---------------------------------------------------------

    def status(self) -> dict[str, Any]:
        """Server info, including whether our API key was accepted ("authenticated")."""
        return self._request("GET", "/").json()

    def read_note(self, path: str) -> str:
        """Return the raw markdown of a note."""
        response = self._request("GET", _vault_url(path), headers={"Accept": MARKDOWN})
        return response.text

    def create_note(self, path: str, content: str) -> None:
        """Create a new note. Raises NoteAlreadyExistsError if it already exists."""
        if self._note_exists(path):
            raise NoteAlreadyExistsError(f"note already exists: {path}")
        self._put(path, content)

    def update_note(self, path: str, content: str) -> None:
        """Replace the whole content of an existing note. Raises NoteNotFoundError if missing."""
        if not self._note_exists(path):
            raise NoteNotFoundError(f"note not found: {path}")
        self._put(path, content)

    def append_note(self, path: str, content: str) -> None:
        """Add content to the end of a note. The plugin creates the note if it is missing."""
        self._request(
            "POST", _vault_url(path), content=content, headers={"Content-Type": MARKDOWN}
        )

    def delete_note(self, path: str) -> None:
        """Delete a note. Obsidian moves it to its trash, so it can be restored."""
        self._request("DELETE", _vault_url(path))

    def list_notes(self, folder: str = "") -> list[str]:
        """Vault-relative paths of the files directly inside ``folder``.

        Subfolders are left out. A folder that does not exist gives [].
        """
        folder = folder.strip().strip("/")
        url = "/vault/" + (quote(folder, safe="/") + "/" if folder else "")
        try:
            response = self._request("GET", url)
        except NoteNotFoundError:
            return []
        prefix = f"{folder}/" if folder else ""
        # The plugin lists names relative to the folder; subfolders end in "/".
        return [prefix + name for name in response.json().get("files", []) if not name.endswith("/")]

    def search_notes(self, query: str, context_length: int = 100) -> list[SearchResult]:
        """Full-text search across the vault."""
        response = self._request(
            "POST",
            "/search/simple/",
            params={"query": query, "contextLength": context_length},
        )
        return [SearchResult.from_json(item) for item in response.json()]

    # --- Internals ----------------------------------------------------------

    def _note_exists(self, path: str) -> bool:
        try:
            self.read_note(path)
        except NoteNotFoundError:
            return False
        return True

    def _put(self, path: str, content: str) -> None:
        self._request("PUT", _vault_url(path), content=content, headers={"Content-Type": MARKDOWN})

    def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        """Send one HTTP request and turn failures into ObsidianError subclasses.

        If Obsidian is not running and a launcher is set, start Obsidian, wait
        for it, and send the request once more.
        """
        try:
            response = self._send(method, url, **kwargs)
        except ObsidianNotRunningError:
            if self._launcher is None:
                raise
            self._start_obsidian()
            response = self._send(method, url, **kwargs)

        if response.is_success:
            return response
        if response.status_code in (401, 403):
            raise ObsidianAuthError("Obsidian rejected the API key; check OBSIDIAN_API_KEY")
        if response.status_code == 404:
            raise NoteNotFoundError(f"not found: {url}")
        error_code, message = _parse_error(response)
        raise ObsidianAPIError(response.status_code, message, error_code)

    def _send(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        log.debug("%s %s", method, url)
        try:
            return self._http.request(method, url, **kwargs)
        except httpx.ConnectError as exc:
            if "CERTIFICATE_VERIFY_FAILED" in str(exc):
                raise ObsidianConnectionError(
                    f"cannot connect to {self.base_url}. HTTPS certificate not trusted; "
                    "set ca_cert / OBSIDIAN_CA_CERT."
                ) from exc
            raise ObsidianNotRunningError(
                f"cannot connect to {self.base_url}. Obsidian isn't open, or its Local REST API "
                "plugin is not enabled."
            ) from exc
        except httpx.TimeoutException as exc:
            raise ObsidianConnectionError(f"Obsidian at {self.base_url} timed out") from exc

    def _start_obsidian(self) -> None:
        # Tool calls can arrive from worker threads; only one of them starts Obsidian.
        with self._launch_lock:
            if not self._is_up():  # another thread may have started it meanwhile
                self._launcher.start_and_wait(self._is_up)

    def _is_up(self) -> bool:
        """True if anything answers at the base URL (GET / needs no API key)."""
        try:
            self._http.get("/", timeout=2)
        except httpx.TransportError:
            return False
        return True


def _vault_url(path: str) -> str:
    """Turn a vault-relative path like 'Daily/2026-10-02.md' into an API URL.

    quote() percent-encodes characters that mean something special in a URL
    (spaces, '#', '?', non-ASCII letters) while keeping '/' as a separator.
    """
    path = path.strip().lstrip("/")
    if not path or path.endswith("/"):
        raise ValueError(f"expected a note path like 'folder/note.md', got {path!r}")
    return "/vault/" + quote(path, safe="/")


def _parse_error(response: httpx.Response) -> tuple[int | None, str]:
    """Read the plugin's JSON error body: {"errorCode": 40149, "message": "..."}."""
    try:
        body = response.json()
        return body.get("errorCode"), body.get("message") or response.reason_phrase
    except ValueError:  # the body was not JSON
        return None, response.text or response.reason_phrase
