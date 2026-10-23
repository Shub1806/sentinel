import asyncio
import json

import httpx2
import pytest

from app.llm import ChatModel, ModelError

TOOLS = [{"type": "function", "function": {"name": "k8s_pods", "parameters": {}}}]


def model_answering(*bodies, status=200):
    requests = []
    answers = iter(bodies)

    def handler(request):
        requests.append(json.loads(request.content))
        return httpx2.Response(status, json=next(answers))

    client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    return ChatModel(client, "http://ollama:11434/v1", "small-model"), requests


def message(**fields):
    return {
        "choices": [{"message": {"role": "assistant", **fields}}],
        "usage": {"total_tokens": 42},
    }


def test_sends_model_messages_and_tools_with_temperature_zero():
    model, requests = model_answering(message(content="hello"))

    reply = asyncio.run(model.chat([{"role": "user", "content": "hi"}], TOOLS))

    assert reply.content == "hello" and reply.tokens == 42
    sent = requests[0]
    assert (sent["model"], sent["temperature"], sent["stream"]) == ("small-model", 0, False)
    assert sent["tools"] == TOOLS


def test_reads_tool_calls_with_json_string_arguments():
    model, _ = model_answering(
        message(
            content="",
            tool_calls=[
                {
                    "id": "c1",
                    "type": "function",
                    "function": {"name": "loki_logs", "arguments": '{"query": "x"}'},
                }
            ],
        )
    )

    reply = asyncio.run(model.chat([], TOOLS))

    call = reply.tool_calls[0]
    assert (call.id, call.name, call.arguments, call.problem) == (
        "c1",
        "loki_logs",
        {"query": "x"},
        None,
    )


def test_broken_arguments_become_a_problem_not_a_crash():
    model, _ = model_answering(
        message(tool_calls=[{"function": {"name": "loki_logs", "arguments": "{query: x"}}])
    )

    call = asyncio.run(model.chat([], TOOLS)).tool_calls[0]

    assert call.arguments == {}
    assert call.problem.startswith("arguments were not valid JSON")
    assert call.id == "call-0"


def test_tool_call_written_as_text_is_accepted():
    text = 'I will check the pods. {"name": "k8s_pods", "parameters": {}}'
    model, _ = model_answering(message(content=text))

    reply = asyncio.run(model.chat([], TOOLS))

    assert [c.name for c in reply.tool_calls] == ["k8s_pods"]
    assert reply.content == ""


def test_text_that_is_not_a_known_tool_stays_text():
    text = '{"summary": "payments is crash-looping"}'
    model, _ = model_answering(message(content=text))

    reply = asyncio.run(model.chat([], TOOLS))

    assert reply.tool_calls == [] and reply.content == text


def test_server_errors_raise_model_error():
    model, _ = model_answering({"error": "model 'x' not found"}, status=404)

    with pytest.raises(ModelError, match="model server said 404"):
        asyncio.run(model.chat([]))


def test_api_key_is_sent_only_when_set():
    seen = []

    def handler(request):
        seen.append(request.headers.get("Authorization"))
        return httpx2.Response(200, json=message(content="ok"))

    client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    asyncio.run(ChatModel(client, "http://x/v1", "m").chat([]))
    asyncio.run(ChatModel(client, "http://x/v1", "m", api_key="k").chat([]))

    assert seen == [None, "Bearer k"]
