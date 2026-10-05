import json

import pytest
from pydantic import BaseModel, ConfigDict, Field

from alfred.tools import Tool, ToolError, ToolRegistry


class AddParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    a: int = Field(description="first number")
    b: int = Field(0, description="second number")


def add(p: AddParams) -> dict:
    return {"sum": p.a + p.b}


@pytest.fixture
def registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(Tool("add", "Add two numbers.", AddParams, add))
    return registry


def test_successful_call_returns_json(registry):
    outcome = registry.call("add", {"a": 2, "b": 3})
    assert outcome.ok
    assert json.loads(outcome.content) == {"sum": 5}


def test_arguments_may_arrive_as_json_text(registry):
    assert json.loads(registry.call("add", '{"a": 1}').content) == {"sum": 1}


def test_arguments_are_validated_before_the_handler_runs():
    called = []
    registry = ToolRegistry()
    registry.register(Tool("add", "Add.", AddParams, lambda p: called.append(p)))

    outcome = registry.call("add", {"a": "not a number"})

    assert not outcome.ok
    assert "Invalid arguments for add" in outcome.content
    assert "a:" in outcome.content
    assert called == []


def test_unknown_arguments_are_rejected(registry):
    outcome = registry.call("add", {"a": 1, "markdown": "# injected"})
    assert not outcome.ok
    assert "markdown" in outcome.content


def test_unknown_tool(registry):
    outcome = registry.call("rm_rf", {})
    assert not outcome.ok
    assert "Unknown tool" in outcome.content


def test_bad_json_text(registry):
    outcome = registry.call("add", "{not json")
    assert not outcome.ok
    assert "not valid JSON" in outcome.content


def test_tool_error_message_is_passed_to_the_model():
    def fail(p):
        raise ToolError("no task with id 'x'")

    registry = ToolRegistry()
    registry.register(Tool("fail", "Fails.", AddParams, fail))
    outcome = registry.call("fail", {"a": 1})
    assert outcome == type(outcome)(False, "no task with id 'x'")


def test_unexpected_crash_is_hidden_from_the_model():
    def crash(p):
        raise RuntimeError("secret internal detail")

    registry = ToolRegistry()
    registry.register(Tool("crash", "Crashes.", AddParams, crash))
    outcome = registry.call("crash", {"a": 1})
    assert not outcome.ok
    assert "internal error" in outcome.content
    assert "secret" not in outcome.content


def test_schema_is_generated_from_the_pydantic_model(registry):
    schema = registry.get("add").parameters_schema()
    assert schema["type"] == "object"
    assert schema["required"] == ["a"]
    assert schema["properties"]["a"]["description"] == "first number"
    assert schema["additionalProperties"] is False


@pytest.mark.parametrize("name", ["", "has space", "x" * 65, "semi;colon"])
def test_invalid_tool_names(name):
    with pytest.raises(ValueError, match="invalid tool name"):
        ToolRegistry().register(Tool(name, "x", AddParams, add))


def test_duplicate_names(registry):
    with pytest.raises(ValueError, match="already registered"):
        registry.register(Tool("add", "again", AddParams, add))


def test_iteration_and_length(registry):
    assert [t.name for t in registry] == ["add"]
    assert len(registry) == 1
