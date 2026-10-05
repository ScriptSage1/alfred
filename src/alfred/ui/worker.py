"""Runs the agent on a background thread so the window never freezes.

Qt has its own event loop (for the window); the agent is async (asyncio). The
worker keeps a separate asyncio loop on its own thread. The UI hands it work
with run(text); results come back as Qt signals, which Qt safely delivers on
the UI thread.

The worker does not know what the agent can do. It is given a function that
opens an agent (alfred.assistant.open_agent in the real app, a fake in tests).
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager, AsyncExitStack
from typing import Any

from PySide6.QtCore import QObject, Signal

log = logging.getLogger(__name__)

AgentOpener = Callable[[], AbstractAsyncContextManager[Any]]


class AgentWorker(QObject):
    event = Signal(object)  # an AgentEvent (progress)
    finished = Signal(str)  # the reply
    failed = Signal(str)    # an error message for the user

    def __init__(self, open_agent: AgentOpener) -> None:
        super().__init__()
        self._open_agent = open_agent
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, name="alfred-agent", daemon=True)
        self._agent: Any = None
        self._stack: AsyncExitStack | None = None
        self._lock: asyncio.Lock | None = None

    def start(self) -> None:
        """Start the thread and warm up the agent (Copilot takes a few seconds)."""
        self._thread.start()
        asyncio.run_coroutine_threadsafe(self._warm_up(), self._loop)

    def run(self, text: str) -> None:
        """Ask the agent; the answer arrives later through `finished` or `failed`."""
        asyncio.run_coroutine_threadsafe(self._run(text), self._loop)

    def stop(self) -> None:
        if not self._thread.is_alive():
            return
        try:
            asyncio.run_coroutine_threadsafe(self._close(), self._loop).result(timeout=15)
        except Exception:
            log.warning("agent did not shut down cleanly", exc_info=True)
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=5)

    async def _run(self, text: str) -> None:
        try:
            agent = await self._get_agent()
            reply = await agent.run(text, on_event=self.event.emit)
        except Exception as exc:
            log.exception("request failed")
            self.failed.emit(str(exc) or type(exc).__name__)
            return
        self.finished.emit(reply)

    async def _warm_up(self) -> None:
        try:
            await self._get_agent()
        except Exception as exc:
            # Not fatal: the next request tries again (e.g. after signing in).
            log.warning("agent not ready yet: %s", exc)

    async def _get_agent(self) -> Any:
        if self._lock is None:
            self._lock = asyncio.Lock()
        async with self._lock:
            if self._agent is None:
                stack = AsyncExitStack()
                try:
                    self._agent = await stack.enter_async_context(self._open_agent())
                except BaseException:
                    await stack.aclose()
                    raise
                self._stack = stack
            return self._agent

    async def _close(self) -> None:
        if self._stack is not None:
            await self._stack.aclose()
        self._agent = self._stack = None
