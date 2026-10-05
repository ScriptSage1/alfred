"""Manual test: is GitHub Copilot ready for Alfred?

    python scripts/check_copilot.py

Starts the Copilot runtime, reports who is signed in and which models you can
use, then asks one tiny question without any tools. Writes nothing anywhere.
"""

from __future__ import annotations

import asyncio
import sys

from alfred.agent import Agent, AgentError
from alfred.agent.copilot_backend import CopilotBackend
from alfred.config import load_config
from alfred.tools import ToolRegistry


async def check() -> int:
    settings = load_config().copilot
    print(f"Model: {settings.model or '(Copilot default)'}")
    try:
        async with CopilotBackend(model=settings.model, timeout=settings.timeout) as backend:
            print("  - Copilot runtime started and signed in")
            models = await backend._client.list_models()
            print(f"  - models available: {', '.join(m.id for m in models) or '(none listed)'}")
            reply = await Agent(backend, ToolRegistry()).run("Reply with exactly: Alfred is connected.")
            print(f"  - Copilot replied: {reply}")
    except AgentError as exc:
        print(f"\nFAILED: {exc}")
        return 1
    print("\nSuccess! Alfred can talk to Copilot.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(check()))
