"""What a tool is, and how a call is checked, run and turned into text for the model."""

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import httpx2
from pydantic import BaseModel, ValidationError


class ToolError(Exception):
    """A tool could not do what was asked. The message is shown to the model."""


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    params: type[BaseModel]
    run: Callable[[BaseModel], Awaitable[Any]]

    def schema(self) -> dict:
        """The tool as a function definition (JSON Schema parameters) for a chat model."""
        parameters = self.params.model_json_schema()
        parameters.pop("title", None)
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": parameters,
            },
        }


def to_text(result: Any, max_chars: int) -> str:
    """Compact JSON, cut to max_chars with a note, so the model knows something is missing."""
    text = json.dumps(result, default=str, separators=(",", ":"))
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"... [cut: {len(text) - max_chars} more characters]"


async def call_tool(tools: dict[str, Tool], name: str, args: dict, max_chars: int) -> str:
    """Runs one tool call. Never raises: problems come back as text the model can react to."""
    tool = tools.get(name)
    if tool is None:
        return f"error: unknown tool {name!r}. Available: {', '.join(sorted(tools))}"
    try:
        params = tool.params.model_validate(args)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in e['loc']) or 'arguments'}: {e['msg']}" for e in exc.errors()
        )
        return f"error: bad arguments for {name}: {problems}"
    try:
        return to_text(await tool.run(params), max_chars)
    except ToolError as exc:
        return f"error: {exc}"
    except httpx2.TimeoutException:
        return f"error: {name} timed out"
    except httpx2.HTTPError as exc:
        return f"error: {name} failed: {exc}"
