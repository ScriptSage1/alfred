"""Tests for the palette, the agent worker and the hotkey.

Qt runs with the "offscreen" platform: real widgets, but nothing is drawn on
your screen, so these tests work anywhere (including CI).
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # must happen before Qt starts

import sys
import threading
import time
from contextlib import asynccontextmanager

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from alfred.agent import Agent, AgentError
from alfred.tools import ToolRegistry
from alfred.ui.app import PaletteController, pretty_hotkey
from alfred.ui.palette import CommandPalette
from alfred.ui.win32 import MOD_ALT, MOD_CONTROL, MOD_SHIFT, GlobalHotkey, HotkeyError, parse_hotkey
from alfred.ui.worker import AgentWorker
from conftest import ScriptedBackend


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def palette(qapp):
    palette = CommandPalette()
    yield palette
    palette.close()


def wait_until(condition, timeout: float = 5.0) -> None:
    """Let Qt process events until `condition()` is true."""
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError("timed out waiting for the UI")
        QApplication.processEvents()
        time.sleep(0.01)


# --- The palette on its own --------------------------------------------------------


def test_enter_submits_the_text(palette):
    sent = []
    palette.submitted.connect(sent.append)
    palette.show_palette()
    QTest.keyClicks(palette._input, "Add a task to finish my assignment tomorrow")
    QTest.keyClick(palette._input, Qt.Key.Key_Return)
    assert sent == ["Add a task to finish my assignment tomorrow"]


def test_empty_text_is_not_submitted(palette):
    sent = []
    palette.submitted.connect(sent.append)
    QTest.keyClicks(palette._input, "   ")
    QTest.keyClick(palette._input, Qt.Key.Key_Return)
    assert sent == []


def test_nothing_is_submitted_while_busy(palette):
    sent = []
    palette.submitted.connect(sent.append)
    palette.begin_request()
    QTest.keyClicks(palette._input, "second request")
    QTest.keyClick(palette._input, Qt.Key.Key_Return)
    assert sent == []
    assert palette._input.isReadOnly()


def test_escape_closes_the_palette(palette):
    palette.show_palette()
    wait_until(lambda: palette.windowOpacity() > 0.99)
    QTest.keyClick(palette._input, Qt.Key.Key_Escape)
    wait_until(lambda: not palette.isVisible())


def test_progress_steps_and_reply(palette):
    palette.begin_request()
    assert "Thinking" in palette._steps_label.text()
    assert palette._activity.running

    palette.add_step("tool_started", "Create task…", "create_task")
    assert "Thinking" not in palette._steps_label.text()
    assert "●" in palette._steps_label.text()

    palette.add_step("tool_finished", "Create task", "create_task")
    assert "✓" in palette._steps_label.text()
    assert "●" not in palette._steps_label.text()

    palette.show_reply("Added 'Finish my assignment', due tomorrow.")
    assert palette._reply_label.text() == "Added 'Finish my assignment', due tomorrow."
    assert not palette.busy
    assert not palette._activity.running
    assert not palette._input.isReadOnly()


def test_failed_step_and_error(palette):
    palette.begin_request()
    palette.add_step("tool_started", "Get task…", "get_task")
    palette.add_step("tool_failed", "Get task: no task with id 'x'", "get_task")
    assert "✕" in palette._steps_label.text()
    palette.show_error("Copilot is not signed in.")
    assert palette._reply_label.text() == "Copilot is not signed in."
    assert not palette.busy


def test_text_is_escaped_not_rendered_as_html(palette):
    palette.begin_request()
    palette.add_step("tool_started", "<b>bold</b>", "t")
    assert "&lt;b&gt;" in palette._steps_label.text()


# --- Palette + worker + agent ------------------------------------------------------


def fake_opener(backend):
    @asynccontextmanager
    async def open_agent():
        yield Agent(backend, ToolRegistry())

    return open_agent


def test_submitting_runs_the_agent_and_shows_the_reply(palette):
    worker = AgentWorker(fake_opener(ScriptedBackend(reply="Task created.")))
    controller = PaletteController(palette, worker)  # keep a reference, or Python deletes it
    worker.start()
    try:
        palette.submitted.emit("Add a task to finish my assignment tomorrow")
        wait_until(lambda: palette._reply_label.text() == "Task created.")
        assert not palette.busy
    finally:
        worker.stop()


def test_agent_failure_is_shown_as_an_error(palette):
    @asynccontextmanager
    async def broken():
        raise AgentError("Copilot is not signed in.")
        yield  # pragma: no cover

    worker = AgentWorker(broken)
    controller = PaletteController(palette, worker)  # noqa: F841
    worker.start()
    try:
        palette.submitted.emit("hello")
        wait_until(lambda: palette._reply_label.text() == "Copilot is not signed in.")
    finally:
        worker.stop()


# --- Hotkey --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, expected",
    [
        ("ctrl+alt+space", (MOD_CONTROL | MOD_ALT, 0x20)),
        ("Ctrl + Shift + A", (MOD_CONTROL | MOD_SHIFT, ord("A"))),
        ("alt+f12", (MOD_ALT, 0x7B)),
    ],
)
def test_parse_hotkey(text, expected):
    assert parse_hotkey(text) == expected


@pytest.mark.parametrize("text", ["", "space", "ctrl+hyper+space", "ctrl+alt+pause"])
def test_invalid_hotkeys(text):
    with pytest.raises(HotkeyError):
        parse_hotkey(text)


def test_pretty_hotkey():
    assert pretty_hotkey("ctrl+alt+space") == "Ctrl+Alt+Space"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only API")
def test_global_hotkey_registers_and_delivers_presses():
    from alfred.ui.win32 import WM_HOTKEY, _HOTKEY_ID, _user32

    pressed = threading.Event()
    # An unusual combination, so the test can't clash with a real shortcut.
    hotkey = GlobalHotkey("ctrl+alt+shift+f11", pressed.set)
    hotkey.start()
    try:
        # A second registration of the same keys must fail clearly.
        with pytest.raises(HotkeyError, match="already used"):
            GlobalHotkey("ctrl+alt+shift+f11", lambda: None).start()

        # Simulate Windows delivering the hotkey message to the hotkey thread.
        _user32().PostThreadMessageW(hotkey._thread_id, WM_HOTKEY, _HOTKEY_ID, 0)
        assert pressed.wait(timeout=2)
    finally:
        hotkey.stop()
    assert not hotkey._thread.is_alive()


# --- Lazy start and single instance --------------------------------------------------


def test_agent_starts_when_the_palette_opens_not_before(palette):
    opened = []

    @asynccontextmanager
    async def open_agent():
        opened.append(True)
        yield Agent(ScriptedBackend(), ToolRegistry())

    worker = AgentWorker(open_agent)
    controller = PaletteController(palette, worker)  # noqa: F841
    worker.start()
    try:
        time.sleep(0.2)
        QApplication.processEvents()
        assert opened == []  # nothing heavy at startup

        palette.show_palette()
        wait_until(lambda: opened == [True])

        palette.show_palette()  # opening again does not start a second agent
        time.sleep(0.2)
        assert opened == [True]
    finally:
        worker.stop()


def test_second_copy_asks_the_first_to_open_the_palette(qapp):
    import uuid

    from alfred.ui.app import SingleInstance

    name = f"alfred-test-{uuid.uuid4().hex}"
    assert SingleInstance(name).notify_running_instance() is False  # nobody running yet

    first = SingleInstance(name)
    activations = []
    first.activated.connect(lambda: activations.append(True))
    first.listen()

    assert SingleInstance(name).notify_running_instance() is True
    wait_until(lambda: activations == [True])


def test_long_progress_lines_wrap_instead_of_being_cut_off(palette):
    assert palette._steps_label.wordWrap()
