"""The desktop app: tray icon + global hotkey + command palette + agent worker.

    hotkey         -> GlobalHotkey (Windows) -> palette.toggle()
    palette opens  -> worker.warm_up()  (Copilot starts while you type)
    Enter          -> palette.submitted(text) -> worker.run(text) -> agent.run(text)
    agent progress -> worker.event / finished / failed -> palette shows it
    started twice  -> the second copy asks the first to open the palette, then exits
"""

from __future__ import annotations

import getpass
import logging
import sys
from pathlib import Path

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QAction, QIcon
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon

from alfred.ui.palette import CommandPalette
from alfred.ui.win32 import GlobalHotkey, HotkeyError
from alfred.ui.worker import AgentOpener, AgentWorker

log = logging.getLogger(__name__)

ICON_PATH = Path(__file__).with_name("alfred.png")


class PaletteController(QObject):
    """Connects the palette to the agent worker. Contains no feature logic."""

    def __init__(self, palette: CommandPalette, worker: AgentWorker, tray: QSystemTrayIcon | None = None):
        super().__init__()
        self.palette, self.worker, self.tray = palette, worker, tray
        palette.submitted.connect(self._submit)
        palette.shown.connect(worker.warm_up)
        worker.event.connect(self._progress)
        worker.finished.connect(self._finished)
        worker.failed.connect(self._failed)

    def _submit(self, text: str) -> None:
        self.palette.begin_request()
        self.worker.run(text)  # conceptually: agent.run(text)

    def _progress(self, event) -> None:
        if event.kind.startswith("tool_"):
            self.palette.add_step(event.kind, event.message, event.tool)

    def _finished(self, reply: str) -> None:
        self.palette.show_reply(reply)
        self._notify_if_hidden(reply)

    def _failed(self, message: str) -> None:
        self.palette.show_error(message)
        self._notify_if_hidden(message, error=True)

    def _notify_if_hidden(self, text: str, error: bool = False) -> None:
        # Escape closes the palette but not the request; tell the user when it ends.
        if self.tray is not None and not self.palette.isVisible():
            icon = QSystemTrayIcon.MessageIcon.Warning if error else QSystemTrayIcon.MessageIcon.Information
            self.tray.showMessage("Alfred", text, icon, 6000)


class SingleInstance(QObject):
    """Keeps Alfred to one copy per Windows user.

    The first copy listens on a local "pipe" (QLocalServer). A second copy
    finds it, sends a nudge, and exits; the first copy then opens its palette.
    """

    activated = Signal()

    def __init__(self, name: str | None = None) -> None:
        super().__init__()
        self.name = name or f"alfred-{getpass.getuser()}"
        self._server: QLocalServer | None = None

    def notify_running_instance(self) -> bool:
        """True if another Alfred is running (and has been told to show itself)."""
        socket = QLocalSocket()
        socket.connectToServer(self.name)
        if not socket.waitForConnected(500):
            return False
        socket.write(b"show")
        socket.waitForBytesWritten(500)
        socket.disconnectFromServer()
        return True

    def listen(self) -> None:
        self._server = QLocalServer(self)
        self._server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)  # only this user
        QLocalServer.removeServer(self.name)  # clear a leftover from a crash
        if not self._server.listen(self.name):
            log.warning("single-instance check unavailable: %s", self._server.errorString())
            return
        self._server.newConnection.connect(self._on_connection)

    def _on_connection(self) -> None:
        while (connection := self._server.nextPendingConnection()) is not None:
            connection.disconnected.connect(connection.deleteLater)
            self.activated.emit()


class _HotkeyBridge(QObject):
    """The hotkey fires on a background thread; a Qt signal carries it to the UI thread."""

    pressed = Signal()


def run_desktop(open_agent: AgentOpener, *, hotkey: str = "ctrl+alt+space") -> int:
    """Run Alfred in the background until "Quit" is chosen from the tray menu."""
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName("Alfred")
    app.setQuitOnLastWindowClosed(False)  # closing the palette must not quit the app
    icon = QIcon(str(ICON_PATH))
    app.setWindowIcon(icon)

    instance = SingleInstance()
    if instance.notify_running_instance():
        log.info("Alfred is already running; asked it to open the palette")
        return 0

    palette = CommandPalette(icon)
    instance.activated.connect(palette.show_palette)
    instance.listen()
    worker = AgentWorker(open_agent)
    tray = QSystemTrayIcon(icon)
    controller = PaletteController(palette, worker, tray)  # noqa: F841 (kept alive by this scope)

    bridge = _HotkeyBridge()
    bridge.pressed.connect(palette.toggle)
    global_hotkey = GlobalHotkey(hotkey, bridge.pressed.emit)
    try:
        global_hotkey.start()
    except HotkeyError as exc:
        QMessageBox.critical(None, "Alfred", f"Alfred could not start:\n\n{exc}")
        return 1

    shortcut = pretty_hotkey(hotkey)
    menu = QMenu()
    open_action = QAction(f"Open Alfred\t{shortcut}", menu)
    open_action.triggered.connect(palette.show_palette)
    quit_action = QAction("Quit", menu)
    quit_action.triggered.connect(app.quit)
    menu.addAction(open_action)
    menu.addSeparator()
    menu.addAction(quit_action)
    tray.setContextMenu(menu)
    tray.setToolTip(f"Alfred — {shortcut}")
    tray.activated.connect(
        lambda reason: palette.toggle() if reason == QSystemTrayIcon.ActivationReason.Trigger else None
    )
    tray.show()

    worker.start()
    tray.showMessage("Alfred is running", f"Press {shortcut} to ask Alfred.", icon, 4000)
    log.info("Alfred desktop is running; press %s", shortcut)
    try:
        return app.exec()
    finally:
        global_hotkey.stop()
        worker.stop()
        tray.hide()


def pretty_hotkey(hotkey: str) -> str:
    """'ctrl+alt+space' -> 'Ctrl+Alt+Space'."""
    return "+".join(part.strip().capitalize() for part in hotkey.split("+"))
