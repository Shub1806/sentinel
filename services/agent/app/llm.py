"""Client for a chat model behind an OpenAI-compatible API (POST /chat/completions).

Works with a local Ollama server (http://<host>:11434/v1) and with hosted services that
speak the same API. Which model to use is a setting, not part of the code.
"""

import json
import re
from dataclasses import dataclass, field

import httpx2


class ModelError(Exception):
    """The model server failed or sent something we can't use."""


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict
    # Set when the arguments were not valid JSON; the call is then answered with this error.
    problem: str | None = None


@dataclass
class Reply:
    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    tokens: int = 0


def _parse_arguments(raw) -> tuple[dict, str | None]:
    if isinstance(raw, dict):
        return raw, None
    if raw in (None, ""):
        return {}, None
    try:
        value = json.loads(raw)
    except TypeError, json.JSONDecodeError:
        return {}, f"arguments were not valid JSON: {str(raw)[:200]}"
    if not isinstance(value, dict):
        return {}, "arguments must be a JSON object"
    return value, None


_JSON_OBJECT = re.compile(r"\{.*\}", re.DOTALL)


def _tool_call_in_text(content: str, tool_names: set[str]) -> ToolCall | None:
    """Small models sometimes write a tool call as plain JSON text instead of using the
    tool-call field. Accept {"name": ..., "arguments"|"parameters": {...}} for a known tool."""
    match = _JSON_OBJECT.search(content or "")
    if not match:
        return None
    try:
        value = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(value, dict) or value.get("name") not in tool_names:
        return None
    args = value.get("arguments", value.get("parameters", {}))
    arguments, problem = _parse_arguments(args)
    return ToolCall(id="text-call", name=value["name"], arguments=arguments, problem=problem)


class ChatModel:
    def __init__(self, client: httpx2.AsyncClient, base_url: str, model: str, api_key: str = ""):
        self.client = client
        self.base_url = base_url
        self.model = model
        self.api_key = api_key

    async def chat(self, messages: list[dict], tools: list[dict] | None = None) -> Reply:
        body = {"model": self.model, "messages": messages, "temperature": 0, "stream": False}
        if tools:
            body["tools"] = tools
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        try:
            response = await self.client.post(
                f"{self.base_url}/chat/completions", json=body, headers=headers
            )
        except httpx2.TimeoutException as exc:
            raise ModelError("the model took too long to answer") from exc
        except httpx2.HTTPError as exc:
            raise ModelError(f"could not reach the model server: {exc}") from exc
        if response.status_code != 200:
            raise ModelError(f"model server said {response.status_code}: {response.text[:300]}")
        try:
            message = response.json()["choices"][0]["message"]
        except (ValueError, KeyError, IndexError) as exc:
            raise ModelError(
                f"unexpected answer from the model server: {response.text[:300]}"
            ) from exc

        calls = []
        for i, call in enumerate(message.get("tool_calls") or []):
            function = call.get("function", {})
            arguments, problem = _parse_arguments(function.get("arguments"))
            calls.append(
                ToolCall(
                    id=call.get("id") or f"call-{i}",
                    name=function.get("name", ""),
                    arguments=arguments,
                    problem=problem,
                )
            )
        content = message.get("content") or ""
        if not calls and tools:
            names = {t["function"]["name"] for t in tools}
            text_call = _tool_call_in_text(content, names)
            if text_call:
                calls, content = [text_call], ""
        tokens = (response.json().get("usage") or {}).get("total_tokens", 0)
        return Reply(content=content, tool_calls=calls, tokens=tokens)
