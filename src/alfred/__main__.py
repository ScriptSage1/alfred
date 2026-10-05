"""Command-line entry point.

    alfred                 start the desktop app (tray icon + Ctrl+Alt+Space palette)
    alfred ask "..."       send one request to the agent and print the reply
    alfred autostart on    start Alfred automatically when you log in (off / status)
    alfred-desktop         same as `alfred`, but without a console window

Runs when you type `alfred` (installed by pyproject.toml) or `python -m alfred`.
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import logging
import sys
from pathlib import Path

from alfred import __version__
from alfred.app import Alfred, ModuleLoadError
from alfred.config import VALID_LOG_LEVELS, AlfredConfig, ConfigError, load_config, resolve_config_path
from alfred.logging_setup import configure_logging, default_log_file


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="alfred", description="Alfred, your personal desktop assistant."
    )
    parser.add_argument(
        "--config",
        type=Path,
        help="path to a TOML config file (default: ./alfred.toml if it exists)",
    )
    parser.add_argument(
        "--log-level",
        choices=VALID_LOG_LEVELS,
        help="override the log level from the config file",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    commands = parser.add_subparsers(dest="command", metavar="command")
    commands.add_parser("start", help="run the desktop app (the default)")
    ask = commands.add_parser("ask", help="send one request to Alfred and print the reply")
    ask.add_argument("message", nargs="+", help="what you want, in plain language")
    autostart = commands.add_parser("autostart", help="start Alfred when you log in to Windows")
    autostart.add_argument("action", choices=["on", "off", "status"])

    args = parser.parse_args(argv)
    args.command = args.command or "start"
    return args


def main(argv: list[str] | None = None) -> int:
    """Start Alfred. Returns a process exit code (0 means success)."""
    args = parse_args(argv)

    try:
        config = load_config(args.config)
    except ConfigError as exc:
        # Logging isn't configured yet, so report straight to stderr.
        print(f"alfred: {exc}", file=sys.stderr)
        return 2

    if args.log_level:
        config = dataclasses.replace(config, log_level=args.log_level)

    if args.command == "autostart":
        return run_autostart(args.action, resolve_config_path(args.config))

    if args.command == "ask":
        # Keep the terminal readable: only warnings unless --log-level was given.
        configure_logging(args.log_level or "WARNING")
        return run_ask(config, " ".join(args.message))

    configure_logging(config.log_level, log_file=default_log_file())
    try:
        Alfred(config).run()  # load the configured modules (phase 1)
    except ModuleLoadError as exc:
        logging.getLogger("alfred").error("%s", exc)
        return 1
    return run_start(config)


def run_start(config: AlfredConfig) -> int:
    # Imported here, not at the top, so `alfred ask` never loads Qt or Copilot.
    from alfred.assistant import open_agent
    from alfred.ui.app import run_desktop

    return run_desktop(lambda: open_agent(config), hotkey=config.ui.hotkey)


def run_autostart(action: str, config_path: Path | None) -> int:
    from alfred import autostart

    try:
        if action == "on":
            command = autostart.desktop_command(config_path)
            autostart.enable(command)
            print(f"Alfred will start when you log in.\n  {command}")
        elif action == "off":
            removed = autostart.disable()
            print("Alfred will no longer start at login." if removed else "Autostart was not on.")
        else:
            command = autostart.current()
            print(f"Autostart is on:\n  {command}" if command else "Autostart is off.")
    except autostart.AutostartError as exc:
        print(f"alfred: {exc}", file=sys.stderr)
        return 1
    return 0


def run_ask(config: AlfredConfig, message: str) -> int:
    from alfred.agent import AgentError
    from alfred.assistant import open_agent
    from alfred.obsidian import ObsidianError

    async def ask() -> str:
        async with open_agent(config) as agent:
            return await agent.run(message, on_event=_print_progress)

    try:
        reply = asyncio.run(ask())
    except (AgentError, ObsidianError) as exc:
        print(f"alfred: {exc}", file=sys.stderr)
        return 1
    print(reply)
    return 0


def _print_progress(event) -> None:
    marks = {"thinking": "...", "tool_started": " ->", "tool_finished": " ok", "tool_failed": "  !"}
    if event.kind in marks:
        print(f"{marks[event.kind]} {event.message}", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
