"""The Tool Registry: Python functions that an AI model is allowed to call.

A Tool is a name, a description, a Pydantic model describing its arguments,
and a handler. The registry is the gatekeeper between the model and Python:

    model asks:   create_task({"title": "...", "deadline": "2026-10-09"})
    registry:     validates the arguments against the Pydantic model
                  calls the handler with a typed object
                  turns the result (or the error) into JSON text for the model

Nothing here knows about Copilot or about tasks. Any feature can register
tools, and any AI backend can call them.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ValidationError

log = logging.getLogger(__name__)

_TOOL_NAME = re.compile(r"[a-zA-Z0-9_-]{1,64}")


class ToolError(Exception):
    """An expected failure whose message should be shown to the model,
    e.g. "no task with id 'x'". The model can read it and try again."""


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    params: type[BaseModel]                 # the arguments, as a Pydantic model
    handler: Callable[[Any], Any]           # receives an instance of `params`

    def parameters_schema(self) -> dict[str, Any]:
        """JSON Schema of the arguments; this is what the model sees."""
        return self.params.model_json_schema()


@dataclass(frozen=True)
class ToolOutcome:
    ok: bool
    content: str  # JSON text (or an error message) for the model


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> Tool:
        if not _TOOL_NAME.fullmatch(tool.name):
            raise ValueError(f"invalid tool name {tool.name!r}: use letters, digits, '_' or '-'")
        if tool.name in self._tools:
            raise ValueError(f"a tool named {tool.name!r} is already registered")
        self._tools[tool.name] = tool
        return tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def __iter__(self) -> Iterator[Tool]:
        return iter(self._tools.values())

    def __len__(self) -> int:
        return len(self._tools)

    def call(self, name: str, arguments: Mapping[str, Any] | str | None) -> ToolOutcome:
        """Validate the arguments, run the tool, and report the result as text.

        Never raises: every failure becomes ToolOutcome(ok=False, ...) so the
        model can see what went wrong.
        """
        tool = self._tools.get(name)
        if tool is None:
            return ToolOutcome(False, f"Unknown tool {name!r}.")

        if isinstance(arguments, str):  # some backends send the raw JSON text
            try:
                arguments = json.loads(arguments or "{}")
            except json.JSONDecodeError as exc:
                return ToolOutcome(False, f"Arguments are not valid JSON: {exc}")

        try:
            params = tool.params.model_validate(arguments or {})
        except ValidationError as exc:
            return ToolOutcome(False, f"Invalid arguments for {name}: {_describe(exc)}")

        try:
            result = tool.handler(params)
        except ToolError as exc:
            log.info("tool %s failed: %s", name, exc)
            return ToolOutcome(False, str(exc))
        except Exception:
            log.exception("tool %s crashed", name)
            return ToolOutcome(False, f"Tool {name} failed because of an internal error.")

        log.info("tool %s succeeded", name)
        return ToolOutcome(True, json.dumps(result, ensure_ascii=False, default=str))


def _describe(exc: ValidationError) -> str:
    """Pydantic's errors, as one short line the model can act on."""
    parts = []
    for error in exc.errors():
        where = ".".join(str(p) for p in error["loc"]) or "arguments"
        parts.append(f"{where}: {error['msg']}")
    return "; ".join(parts)
