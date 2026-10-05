import logging
from contextlib import asynccontextmanager

import pytest

import alfred.assistant
import alfred.ui.app
from alfred.__main__ import main
from alfred.agent import Agent, AgentError
from alfred.tools import ToolRegistry
from conftest import ScriptedBackend


@pytest.fixture(autouse=True)
def no_real_desktop(monkeypatch, tmp_path):
    """Never open the real desktop app or write to the real log folder in tests."""
    started = []
    monkeypatch.setattr(alfred.ui.app, "run_desktop", lambda opener, hotkey: started.append(hotkey) or 0)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    saved = logging.root.handlers[:], logging.root.level
    yield started
    # main() reconfigures logging; put pytest's own handlers back afterwards.
    for handler in logging.root.handlers:
        if handler not in saved[0]:
            handler.close()
    logging.root.handlers[:], logging.root.level = saved


def test_start_loads_modules_then_runs_the_desktop(tmp_path, monkeypatch, capsys, no_real_desktop):
    monkeypatch.chdir(tmp_path)  # no alfred.toml here, so defaults are used
    assert main([]) == 0
    assert "Hello, world!" in capsys.readouterr().out
    assert no_real_desktop == ["ctrl+alt+space"]
    assert (tmp_path / "Alfred" / "alfred.log").exists()


def test_start_with_config_file(tmp_path, capsys, no_real_desktop):
    path = tmp_path / "custom.toml"
    path.write_text('[alfred]\nmodules = []\n[ui]\nhotkey = "ctrl+shift+k"\n', encoding="utf-8")

    assert main(["--config", str(path), "start"]) == 0
    assert "Hello" not in capsys.readouterr().out  # hello was not loaded
    assert no_real_desktop == ["ctrl+shift+k"]


def test_main_missing_config_returns_2(tmp_path, capsys):
    assert main(["--config", str(tmp_path / "nope.toml")]) == 2
    assert "not found" in capsys.readouterr().err


def test_main_unknown_module_returns_1(tmp_path, capsys, no_real_desktop):
    path = tmp_path / "custom.toml"
    path.write_text('[alfred]\nmodules = ["nope"]\n', encoding="utf-8")

    assert main(["--config", str(path)]) == 1
    assert "no Alfred module named 'nope'" in capsys.readouterr().err
    assert no_real_desktop == []  # the desktop never started


def test_autostart_on_registers_the_desktop_command_with_the_config(tmp_path, monkeypatch, capsys):
    import alfred.autostart

    saved = {}
    monkeypatch.setattr(alfred.autostart, "enable", lambda command: saved.update(command=command))
    config = tmp_path / "alfred.toml"
    config.write_text("", encoding="utf-8")

    assert main(["--config", str(config), "autostart", "on"]) == 0
    assert f'--config {config.resolve()}' in saved["command"] or f'--config "{config.resolve()}"' in saved["command"]
    assert "will start when you log in" in capsys.readouterr().out


def test_autostart_status_and_off(monkeypatch, capsys):
    import alfred.autostart

    monkeypatch.setattr(alfred.autostart, "current", lambda: None)
    monkeypatch.setattr(alfred.autostart, "disable", lambda: True)
    assert main(["autostart", "status"]) == 0
    assert main(["autostart", "off"]) == 0
    out = capsys.readouterr().out
    assert "Autostart is off." in out
    assert "will no longer start at login" in out


def test_ask_prints_progress_and_reply(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)

    @asynccontextmanager
    async def fake_open_agent(config):
        yield Agent(ScriptedBackend(reply="Added it."), ToolRegistry())

    monkeypatch.setattr(alfred.assistant, "open_agent", fake_open_agent)

    assert main(["ask", "add", "a", "task"]) == 0
    out, err = capsys.readouterr()
    assert out.strip() == "Added it."
    assert "Thinking" in err


def test_ask_reports_agent_errors(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)

    @asynccontextmanager
    async def not_signed_in(config):
        raise AgentError("Copilot is not signed in.")
        yield  # pragma: no cover

    monkeypatch.setattr(alfred.assistant, "open_agent", not_signed_in)

    assert main(["ask", "hello"]) == 1
    assert "Copilot is not signed in." in capsys.readouterr().err
