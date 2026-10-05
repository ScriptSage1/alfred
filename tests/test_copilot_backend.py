"""CopilotBackend against a fake Copilot SDK client.

The fake records exactly what Alfred asks the SDK to do, and plays back tool
calls the way the real runtime would: by invoking the tool handlers we gave it.
"""

import asyncio
import json
from types import SimpleNamespace

import pytest
from copilot.generated.rpc import PermissionDecisionReject
from copilot.tools import ToolInvocation
from pydantic import BaseModel

from alfred.agent import Agent, AgentError
from alfred.agent.copilot_backend import CopilotBackend
from alfred.tools import Tool, ToolRegistry


class FakeSession:
    def __init__(self, client, options):
        self.client = client
        self.options = options

    async def send_and_wait(self, prompt, *, timeout):
        self.client.prompts.append(prompt)
        if self.client.fail_with:
            raise self.client.fail_with
        tools = {t.name: t for t in self.options["tools"]}
        for name, arguments in self.client.script:
            invocation = ToolInvocation(tool_name=name, arguments=arguments)
            self.client.results.append(await tools[name].handler(invocation))
        return SimpleNamespace(data=SimpleNamespace(content=self.client.reply))

    async def disconnect(self):
        self.client.disconnected += 1


class FakeCopilotClient:
    def __init__(self, *, authenticated=True, script=(), reply="All done.", fail_with=None):
        self.authenticated = authenticated
        self.script = list(script)
        self.reply = reply
        self.fail_with = fail_with
        self.prompts, self.results = [], []
        self.options = None
        self.disconnected = 0
        self.started = self.stopped = False

    async def start(self):
        self.started = True

    async def get_auth_status(self):
        return SimpleNamespace(isAuthenticated=self.authenticated, login="octocat")

    async def create_session(self, **options):
        self.options = options
        return FakeSession(self, options)

    async def stop(self):
        self.stopped = True


class NoteParams(BaseModel):
    text: str


def make_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(Tool("save_note", "Save a note.", NoteParams, lambda p: {"saved": p.text}))
    return registry


def run_agent(client: FakeCopilotClient, message: str = "hi") -> str:
    async def go():
        async with CopilotBackend(client_factory=lambda: client, model="test-model") as backend:
            return await Agent(backend, make_registry()).run(message)

    return asyncio.run(go())


def test_reply_comes_back_from_copilot():
    client = FakeCopilotClient(reply="Hello from Copilot.")
    assert run_agent(client, "hi") == "Hello from Copilot."
    assert client.prompts == ["hi"]
    assert client.started and client.stopped
    assert client.disconnected == 1


def test_session_only_exposes_alfreds_tools():
    client = FakeCopilotClient()
    run_agent(client)
    options = client.options

    assert options["available_tools"].to_list() == ["custom:save_note"]  # no shell, no files
    assert [t.name for t in options["tools"]] == ["save_note"]
    assert options["tools"][0].skip_permission is True
    assert options["tools"][0].parameters["properties"]["text"]["type"] == "string"
    assert options["model"] == "test-model"


def test_instructions_are_appended_to_copilots_system_message():
    client = FakeCopilotClient()
    run_agent(client)
    system = client.options["system_message"]
    assert system["mode"] == "append"  # keeps Copilot's own safety rules
    assert "You are Alfred" in system["content"]


def test_any_other_permission_request_is_rejected():
    client = FakeCopilotClient()
    run_agent(client)
    decision = client.options["on_permission_request"](object(), {})
    assert isinstance(decision, PermissionDecisionReject)


def test_tool_calls_are_forwarded_to_the_registry():
    client = FakeCopilotClient(script=[("save_note", {"text": "milk"})])
    run_agent(client)
    result = client.results[0]
    assert result.result_type == "success"
    assert json.loads(result.text_result_for_llm) == {"saved": "milk"}


def test_invalid_tool_arguments_come_back_as_a_failure():
    client = FakeCopilotClient(script=[("save_note", {"txt": "typo"})])
    run_agent(client)
    result = client.results[0]
    assert result.result_type == "failure"
    assert "Invalid arguments" in result.text_result_for_llm


def test_not_signed_in():
    client = FakeCopilotClient(authenticated=False)
    with pytest.raises(AgentError, match="COPILOT_GITHUB_TOKEN"):
        asyncio.run(CopilotBackend(client_factory=lambda: client).start())
    assert client.stopped


def test_timeout_becomes_agent_error_and_session_is_closed():
    client = FakeCopilotClient(fail_with=TimeoutError())
    with pytest.raises(AgentError, match="did not answer"):
        run_agent(client)
    assert client.disconnected == 1


def test_complete_before_start():
    backend = CopilotBackend(client_factory=FakeCopilotClient)

    async def call_tool(name, args):
        raise AssertionError("not reached")

    with pytest.raises(AgentError, match="not started"):
        asyncio.run(backend.complete(instructions="", message="hi", tools=[], call_tool=call_tool))
