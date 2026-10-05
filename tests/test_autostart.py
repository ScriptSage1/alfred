import sys
from pathlib import Path

import pytest

from alfred import autostart

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows registry")

# A throwaway key, so tests never touch the real Run key.
TEST_KEY = r"Software\AlfredTests\Run"


@pytest.fixture(autouse=True)
def clean_test_key():
    yield
    import winreg

    for key in (TEST_KEY, r"Software\AlfredTests"):
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key)
        except FileNotFoundError:
            pass


def test_enable_status_disable():
    assert autostart.current(key=TEST_KEY) is None

    autostart.enable('"C:\\alfred\\alfred-desktop.exe" --config "C:\\alfred\\alfred.toml"', key=TEST_KEY)
    assert autostart.current(key=TEST_KEY) == '"C:\\alfred\\alfred-desktop.exe" --config "C:\\alfred\\alfred.toml"'

    assert autostart.disable(key=TEST_KEY) is True
    assert autostart.current(key=TEST_KEY) is None
    assert autostart.disable(key=TEST_KEY) is False  # already off


def test_desktop_command_uses_the_windowed_launcher_and_a_fixed_config():
    command = autostart.desktop_command(Path(r"C:\Users\me\My Alfred\alfred.toml"))
    scripts = Path(sys.executable).parent
    assert command.startswith(str(scripts))
    assert "alfred-desktop.exe" in command or "pythonw.exe" in command
    assert command.endswith(r'--config "C:\Users\me\My Alfred\alfred.toml"')  # quoted: it has a space


def test_desktop_command_without_config():
    assert "--config" not in autostart.desktop_command(None)
