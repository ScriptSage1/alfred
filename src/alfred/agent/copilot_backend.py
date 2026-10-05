"""GitHub Copilot as Alfred's AI backend.

This is the ONLY file in Alfred that imports the Copilot SDK (`copilot`,
package github-copilot-sdk). Swapping AI providers means writing another class
with the same `complete()` method; nothing else changes.

How a request flows through the SDK:
1. CopilotClient starts the bundled Copilot runtime once (a separate process).
2. Each request opens a fresh session that can see ONLY Alfred's tools:
   `available_tools` is an allowlist, so Copilot's built-in shell, file and
   web tools are switched off. Copilot cannot reach Obsidian (or anything
   else) except through the registry's validated tools.
3. When the model calls a tool, the SDK runs our handler, which forwards the
   call to the Agent (and so to the ToolRegistry) and returns the result.
4. send_and_wait() returns once the model has written its final reply.

Authentication is handled by the runtime: it uses the COPILOT_GITHUB_TOKEN
(or GH_TOKEN / GITHUB_TOKEN) environment variable, or a stored Copilot CLI
login. Alfred never reads or stores the token.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from typing import Any

from copilot import CopilotClient, ToolSet
from copilot.generated.rpc import PermissionDecisionReject
from copilot.tools import Tool as CopilotTool
from copilot.tools import ToolInvocation, ToolResult

from alfred.agent.agent import AgentError, ToolCaller
from alfred.tools import Tool

log = logging.getLogger(__name__)

SIGN_IN_HELP = (
    "Copilot is not signed in. Create a fine-grained GitHub token with the "
    "'Copilot Requests' permission and store it in the COPILOT_GITHUB_TOKEN environment variable."
)


class CopilotBackend:
    def __init__(
        self,
        *,
        model: str | None = None,
        timeout: float = 120.0,
        client_factory: Callable[[], Any] | None = None,
    ) -> None:
        self.model = model
        self.timeout = timeout
        self._client_factory = client_factory or (lambda: CopilotClient(log_level="error"))
        self._client: Any = None

    async def start(self) -> None:
        """Start the Copilot runtime and check that we are signed in."""
        client = self._client_factory()
        try:
            await client.start()
            status = await client.get_auth_status()
        except Exception as exc:
            raise AgentError(f"could not start Copilot: {exc}") from exc
        if not getattr(status, "isAuthenticated", False):
            await client.stop()
            raise AgentError(SIGN_IN_HELP)
        self._client = client
        log.info("Copilot ready (signed in as %s)", getattr(status, "login", "?"))

    async def stop(self) -> None:
        if self._client is not None:
            client, self._client = self._client, None
            try:
                await client.stop()
            except Exception:
                log.warning("Copilot did not stop cleanly", exc_info=True)

    async def __aenter__(self) -> CopilotBackend:
        await self.start()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.stop()

    async def complete(
        self,
        *,
        instructions: str,
        message: str,
        tools: Sequence[Tool],
        call_tool: ToolCaller,
    ) -> str:
        if self._client is None:
            raise AgentError("Copilot backend is not started")

        allowed = ToolSet()
        for tool in tools:
            allowed.add_custom(tool.name)
        if not tools:
            allowed.add_custom("*")  # matches no tool, but still blocks the built-in ones

        try:
            session = await self._client.create_session(
                model=self.model,
                tools=[_to_copilot_tool(tool, call_tool) for tool in tools],
                available_tools=allowed,
                system_message={"mode": "append", "content": instructions},
                on_permission_request=_deny_everything,
            )
        except Exception as exc:
            raise AgentError(f"Copilot could not start a session: {exc}") from exc

        try:
            event = await session.send_and_wait(message, timeout=self.timeout)
        except TimeoutError as exc:
            raise AgentError(f"Copilot did not answer within {self.timeout:.0f} seconds") from exc
        except Exception as exc:
            raise AgentError(f"Copilot failed: {exc}") from exc
        finally:
            try:
                await session.disconnect()
            except Exception:
                log.debug("session disconnect failed", exc_info=True)

        return getattr(getattr(event, "data", None), "content", None) or ""


def _to_copilot_tool(tool: Tool, call_tool: ToolCaller) -> CopilotTool:
    """Describe an Alfred tool in the SDK's terms. The handler only forwards."""

    async def handler(invocation: ToolInvocation) -> ToolResult:
        outcome = await call_tool(tool.name, invocation.arguments)
        if outcome.ok:
            return ToolResult(text_result_for_llm=outcome.content, result_type="success")
        return ToolResult(
            text_result_for_llm=outcome.content, result_type="failure", error=outcome.content
        )

    return CopilotTool(
        name=tool.name,
        description=tool.description,
        parameters=tool.parameters_schema(),
        handler=handler,
        skip_permission=True,  # our own tools validate their input; no prompt needed
    )


def _deny_everything(request: Any, invocation: Any) -> PermissionDecisionReject:
    # Anything that still asks for permission (shell, files, URLs) is not an Alfred tool.
    return PermissionDecisionReject(feedback="Alfred only allows its own tools.")
