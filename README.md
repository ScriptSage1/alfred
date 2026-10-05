<p align="center">
  <img src="assets/alfred-logo.png" alt="Alfred logo" width="200">
</p>

# Alfred

A personal desktop AI assistant (learning project). Press a hotkey, type a
request in plain language, and Alfred's agent (GitHub Copilot) carries it out
with Alfred's own tools — for now, managing tasks stored in your Obsidian vault.

```
Alt+M → command palette → Agent → Copilot → tool (e.g. create_task)
      → TodoService → ObsidianClient → Obsidian → reply in the palette
```

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
```

Then connect Obsidian and Copilot (sections below).

## Run

```bash
alfred                       # desktop app: tray icon + hotkey palette (logs to %LOCALAPPDATA%\Alfred\alfred.log)
alfred-desktop               # same, without a console window
alfred ask "Add a task to finish my assignment tomorrow"   # one request from the terminal
alfred --log-level DEBUG ask "What tasks are due this week?"
```

The hotkey is set in `alfred.toml` (`[ui] hotkey`). Alfred's default is
Ctrl+Alt+Space; this machine uses `alt+m` because Ctrl+Alt+Space is taken by
another application. Press Esc to close the palette; quit from the tray icon.

### Start automatically at login

```bash
alfred autostart on       # run from the project folder, so it records this alfred.toml
alfred autostart status
alfred autostart off
```

This adds Alfred to your Windows startup apps (per user, no admin rights; you
can also switch it off in Task Manager → Startup apps). Alfred then waits in
the tray using little memory, and Copilot only starts the first time you open
the palette. Starting Alfred while it is already running just opens the palette.

## Test

```bash
pytest
```

## Connecting to GitHub Copilot

Alfred uses the official [GitHub Copilot SDK for Python](https://github.com/github/copilot-sdk)
(`github-copilot-sdk`). It downloads its runtime automatically the first time
it starts. You need a Copilot subscription and one way of signing in:

- **Browser login (no token to manage):** `npm install -g @github/copilot`,
  then `copilot login`. The login is saved in Windows Credential Manager and
  Alfred's Copilot runtime finds it automatically.
- **Fine-grained token:**
  1. On GitHub: Settings → Developer settings → Personal access tokens →
     Fine-grained tokens → Generate new token. Resource owner: **your personal
     account**; Repository access: *Public repositories*; under Permissions →
     Account, add **Copilot Requests**. (Classic `ghp_` tokens are not accepted.)
  2. Store it as an environment variable (never in `alfred.toml`). In PowerShell:
     ```powershell
     $t = Read-Host "GitHub token" -AsSecureString; [Environment]::SetEnvironmentVariable("COPILOT_GITHUB_TOKEN", [System.Net.NetworkCredential]::new("", $t).Password, "User")
     ```
     Programs started afterwards see it; restart Alfred if it is running.

Check it:
   ```bash
   python scripts/check_copilot.py
   ```

Copilot only sees Alfred's registered tools: its built-in shell, file and web
tools are switched off, so it can act only through validated Python code.

## Connecting to Obsidian

Alfred talks to Obsidian through the
[Local REST API](https://github.com/coddingtonbear/obsidian-local-rest-api) community plugin.

1. In Obsidian: Settings → Community plugins → install and enable **Local REST API**.
2. Copy the API key from Settings → Local REST API.
3. Give Alfred the key as an environment variable (never put it in `alfred.toml`):
   ```powershell
   [Environment]::SetEnvironmentVariable("OBSIDIAN_API_KEY", "<your key>", "User")
   ```
   Open a new terminal afterwards so it picks up the variable.
4. Download the plugin's HTTPS certificate into the project folder (Obsidian must be open):
   ```bash
   curl -k https://127.0.0.1:27124/obsidian-local-rest-api.crt -o obsidian-local-rest-api.crt
   ```
5. Run the manual check, which creates `Alfred/connection-test.md` in your vault:
   ```bash
   python scripts/check_obsidian.py
   ```

Settings live in the `[obsidian]` section of `alfred.toml`; `OBSIDIAN_URL` and
`OBSIDIAN_CA_CERT` environment variables override them.

## Tasks

Alfred stores each task as one Markdown file in the vault's `To-do/` folder,
named after the task's id (e.g. `To-do/finish-sih-documentation.md`). Python
(`alfred/todo/markdown.py`) controls the format; every file has the same keys
in the same order (the `#` comments are only here to explain the values):

```markdown
---
id: "finish-sih-documentation"
title: "Finish SIH documentation"
status: "open"            # open | in-progress | done | cancelled
priority: "high"          # low | medium | high
deadline: 2026-10-09
tags:
  - "sih"
reminder: "daily"         # none | once | daily | weekly
reminder_time: "09:00"
reminder_date: null       # for "once"
reminder_days: []         # for "weekly", e.g. ["mon", "thu"]
related:
  - "[[sih-slides]]"
created: 2026-10-05T09:30:00
updated: 2026-10-05T09:30:00
completed: null
---

# Finish SIH documentation

Free-form details.
```

You can edit task files in Obsidian; Alfred re-writes them in this format the
next time it saves them. To check task handling against your vault:

```bash
python scripts/check_todos.py --keep
```

## How the code is organised

| Package | Role |
|---|---|
| `alfred/ui/` | PySide6 command palette, tray icon, Windows hotkey. Knows nothing about tasks. |
| `alfred/agent/agent.py` | Generic `Agent.run(message)`: sends the message plus the registered tools to a backend. |
| `alfred/agent/copilot_backend.py` | The only file that imports the Copilot SDK. |
| `alfred/tools/` | The Tool Registry: validates tool arguments (Pydantic) and runs the Python function. |
| `alfred/todo/` | Todo model, Markdown format, `TodoService`, and the task tools. |
| `alfred/obsidian.py` | The only file that makes HTTP requests. |
| `alfred/assistant.py` | Wires everything together (the composition root). |

`tests/test_architecture.py` enforces these boundaries.
