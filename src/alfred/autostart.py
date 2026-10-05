"""Start Alfred automatically when you log in to Windows.

Windows runs every command listed under the registry key
HKEY_CURRENT_USER\\Software\\Microsoft\\Windows\\CurrentVersion\\Run when you
log in. It is per user, needs no admin rights, and is what Task Manager's
"Startup apps" page shows (where you can also switch Alfred off).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "Alfred"


class AutostartError(Exception):
    """Autostart is not available or could not be changed."""


def desktop_command(config_path: Path | None) -> str:
    """The command Windows should run at login: the windowed Alfred with a fixed config."""
    scripts = Path(sys.executable).parent  # the virtual environment's Scripts folder
    launcher = scripts / "alfred-desktop.exe"
    parts = [str(launcher)] if launcher.exists() else [str(scripts / "pythonw.exe"), "-m", "alfred"]
    if config_path is not None:
        parts += ["--config", str(config_path)]
    return subprocess.list2cmdline(parts)  # quotes paths that contain spaces


def enable(command: str, *, key: str = RUN_KEY) -> None:
    winreg = _winreg()
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key) as run:
        winreg.SetValueEx(run, VALUE_NAME, 0, winreg.REG_SZ, command)


def disable(*, key: str = RUN_KEY) -> bool:
    """Remove the startup entry. Returns False if there was none."""
    winreg = _winreg()
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key, 0, winreg.KEY_SET_VALUE) as run:
            winreg.DeleteValue(run, VALUE_NAME)
    except FileNotFoundError:
        return False
    return True


def current(*, key: str = RUN_KEY) -> str | None:
    """The registered startup command, or None."""
    winreg = _winreg()
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as run:
            value, _ = winreg.QueryValueEx(run, VALUE_NAME)
    except FileNotFoundError:
        return None
    return value


def _winreg():
    if sys.platform != "win32":
        raise AutostartError("starting at login is only implemented for Windows")
    import winreg

    return winreg
