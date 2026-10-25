"""Tests for the replay tool, with a scripted model instead of a model server.
Run from the repo root: services/agent/.venv/bin/python -m pytest -q scripts/eval"""

import asyncio
import json

import replay
from app.llm import Reply, ToolCall

DIFF = json.dumps(
    {
        "deployment": "payments",
        "changes": ["payments: env MAX_AMOUNT_CENTS removed (was abc)"],
        "new_pods_healthy": True,
    },
    separators=(",", ":"),
)
LOGS = '{"lines":[{"time":"05:04:50","line":"skipping invalid order.created message"}]}'


def report_json(kind: str, cause: str) -> str:
    return json.dumps(
        {
            "summary": "payments skips invalid messages",
            "likely_cause": cause,
            "evidence": ["WARN skipping invalid order.created message"],
            "suggested_action": {"kind": kind, "target": "", "detail": ""},
            "confidence": "medium",
        }
    )


def live_run() -> dict:
    """A recorded poison run, cut down to what the replay reads."""
    alert = {
        "fingerprint": "abc",
        "name": "InvalidMessagesReceived",
        "status": "firing",
        "labels": {"alertname": "InvalidMessagesReceived", "job": "payments"},
        "annotations": {"summary": "payments skipped invalid messages"},
        "starts_at": "2026-10-25T05:04:52.641000Z",
        "ends_at": None,
    }
    steps = [
        {
            "tool": "k8s_pods",
            "arguments": {},
            "result": '{"healthy_pods":8}',
            "seconds": 0.1,
            "auto": True,
        },
        {
            "tool": "loki_logs",
            "arguments": {"query": '{namespace="sentinel", app="payments"}'},
            "result": LOGS,
            "seconds": 0.2,
            "auto": True,
        },
        {
            "tool": "k8s_rollout_diff",
            "arguments": {"deployment": "payments"},
            "result": DIFF,
            "seconds": 0.1,
            "auto": True,
        },
    ]
    investigation = {
        "model": "qwen3:4b",
        "started_at": "2026-10-25T05:05:02.770796Z",
        "steps": steps,
        "report": {
            "likely_cause": "Removal of MAX_AMOUNT_CENTS in the latest payments rollout",
            "suggested_action": {"kind": "rollout_undo", "target": "payments"},
        },
        "rule_verdict": {"kind": "none", "target": "", "cause": "a producer sent bad events"},
    }
    detail = {
        "id": 4,
        "title": "InvalidMessagesReceived",
        "status": "firing",
        "opened_at": "2026-10-25T05:05:02.746319Z",
        "alerts": [alert],
    }
    return {"scenario": "poison", "run": 1, "detail": detail, "investigation": investigation}


class ScriptedModel:
    """Plays back prepared replies and remembers what it was sent."""

    model = "scripted"

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    async def chat(self, messages, tools=None):
        self.calls.append(([dict(m) for m in messages], tools))
        return self.replies.pop(0)


def test_rebuilds_the_first_message_and_scores_the_answer():
    model = ScriptedModel(Reply(content=report_json("none", "a producer sent invalid events")))
    row = asyncio.run(replay.replay(live_run(), model))

    messages, tools = model.calls[0]
    first = messages[1]["content"]
    assert "Current time (UTC): 2026-10-25 05:05:02" in first  # the live run's time, not now
    assert "Alert InvalidMessagesReceived (firing)" in first
    assert "Initial findings" in first and DIFF in first and LOGS in first
    # The model sees the agent's real tool definitions.
    assert {t["function"]["name"] for t in tools} >= {"k8s_rollout_diff", "prometheus_query"}
    assert row["agent_cause_ok"] and row["agent_action_ok"]
    assert row["model"] == "scripted" and row["live_model"] == "qwen3:4b"
    assert row["model_tool_calls"] == [] and row["unrecorded_calls"] == []
    # The fixed rules run on the recorded findings too.
    assert row["investigation"]["rule_verdict"]["kind"] == "none"


def test_tool_calls_are_answered_from_the_recording():
    model = ScriptedModel(
        Reply(
            content="",
            tool_calls=[
                ToolCall("1", "k8s_rollout_diff", {"deployment": "payments"}),
                ToolCall("2", "k8s_get", {"kind": "postgres-cluster", "name": "orders-db"}),
            ],
        ),
        Reply(content=report_json("rollout_undo", "the latest payments rollout")),
    )
    row = asyncio.run(replay.replay(live_run(), model))

    messages, _ = model.calls[1]
    answers = [m["content"] for m in messages if m["role"] == "tool"]
    assert answers[0] == DIFF  # same text the live tool returned
    assert answers[1].startswith("error: no recorded result")
    assert row["unrecorded_calls"] == [
        {"tool": "k8s_get", "arguments": {"kind": "postgres-cluster", "name": "orders-db"}}
    ]
    assert len(row["model_tool_calls"]) == 2
    assert not row["agent_cause_ok"] and not row["agent_action_ok"]


def test_report_compares_live_and_replayed_runs(tmp_path):
    results, out = tmp_path / "results", tmp_path / "replay"
    results.mkdir()
    live = live_run()
    (results / "poison-1.json").write_text(json.dumps(live))
    model = ScriptedModel(Reply(content=report_json("none", "a producer sent invalid events")))
    row = asyncio.run(replay.replay(live, model))
    (out / "scripted").mkdir(parents=True)
    (out / "scripted" / "poison-1.json").write_text(json.dumps(row))

    text = replay.report(results, out)

    assert "| Poison messages | 0% (0/1) / 0% (0/1) | 100% (1/1) / 100% (1/1) |" in text
    assert "replay scripted: 1 runs" in text
    assert "| poison-1 | Removal of MAX_AMOUNT_CENTS" in text
    assert (out / "summary.md").read_text() == text
