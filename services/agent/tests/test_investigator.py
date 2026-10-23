import asyncio
import json
from datetime import UTC, datetime

from pydantic import BaseModel

from app.alerts import Notification
from app.incidents import IncidentStore
from app.investigator import Investigation, Investigator, describe_incident
from app.llm import ModelError, Reply, ToolCall
from app.prompts import JSON_ONLY, OUT_OF_STEPS
from app.tools.base import Tool
from tests.payloads import alert, notification

NOW = datetime(2026, 10, 22, 23, 30, tzinfo=UTC)
REPORT = json.dumps(
    {
        "summary": "payments skips bad events",
        "likely_cause": "a producer sent invalid order.created events",
        "evidence": ["3 WARN lines"],
        "suggested_action": {"kind": "none", "target": "", "detail": ""},
        "confidence": "medium",
    }
)


class ScriptedModel:
    """Plays back prepared replies and remembers what it was sent."""

    model = "scripted"

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls: list[tuple[list, list | None]] = []

    async def chat(self, messages, tools=None):
        self.calls.append(([dict(m) for m in messages], tools))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


class Q(BaseModel):
    query: str


def make_tools(log: list):
    async def fake_logs(p: Q):
        log.append(p.query)
        return {"lines": ["WARN skipping invalid order.created message"]}

    return {"loki_logs": Tool("loki_logs", "search logs", Q, fake_logs)}


def incident():
    store = IncidentStore()
    inc, _ = store.record(Notification.model_validate(notification(alert("payments"))))
    return inc


def run(model, tools=None, max_steps=8, timeout=30.0):
    used = []
    investigator = Investigator(
        tools or make_tools(used), 6000, max_steps, timeout, lambda: NOW, triage=False
    )
    inv = Investigation(id=1, incident_id=1, model=model.model)
    asyncio.run(investigator.run(incident(), model, inv))
    return inv, used


def call(name="loki_logs", **args):
    return Reply(content="", tool_calls=[ToolCall(id="c1", name=name, arguments=args)], tokens=10)


def test_calls_a_tool_then_reports():
    model = ScriptedModel(call(query='{namespace="sentinel"} |= "invalid"'), Reply(REPORT, [], 5))

    inv, used = run(model)

    assert inv.status == "done"
    assert inv.report.likely_cause == "a producer sent invalid order.created events"
    assert used == ['{namespace="sentinel"} |= "invalid"']
    assert [s.tool for s in inv.steps] == ["loki_logs"]
    assert inv.tokens == 15
    # The second request carries the tool call and its result, linked by id.
    messages = model.calls[1][0]
    assert messages[-2]["tool_calls"][0]["function"]["name"] == "loki_logs"
    assert messages[-1] == {
        "role": "tool",
        "tool_call_id": "c1",
        "content": '{"lines":["WARN skipping invalid order.created message"]}',
    }


def test_first_message_describes_the_alert_and_tools_are_offered():
    model = ScriptedModel(Reply(REPORT, []))

    run(model)

    messages, tools = model.calls[0]
    assert messages[0]["role"] == "system"
    assert "InvalidMessagesReceived" in messages[1]["content"]
    assert '"job": "payments"' in messages[1]["content"]
    assert [t["function"]["name"] for t in tools] == ["loki_logs"]


def test_tool_errors_go_back_to_the_model():
    model = ScriptedModel(call(name="no_such_tool"), call(), Reply(REPORT, []))

    inv, _ = run(model)

    assert inv.steps[0].result.startswith("error: unknown tool 'no_such_tool'")
    assert inv.steps[1].result.startswith("error: bad arguments for loki_logs")
    assert inv.status == "done"


def test_bad_json_arguments_are_answered_with_the_problem():
    broken = Reply("", [ToolCall("c1", "loki_logs", {}, problem="arguments were not valid JSON")])
    model = ScriptedModel(broken, Reply(REPORT, []))

    inv, used = run(model)

    assert inv.steps[0].result == "error: arguments were not valid JSON"
    assert used == []  # the tool itself was never run


def test_asks_once_for_json_when_the_answer_is_prose():
    model = ScriptedModel(Reply("Payments got bad messages.", []), Reply(REPORT, []))

    inv, _ = run(model)

    assert inv.report is not None
    assert model.calls[1][0][-1] == {"role": "user", "content": JSON_ONLY}


def test_prose_twice_is_kept_as_raw_answer():
    model = ScriptedModel(Reply("Payments got bad messages.", []), Reply("Still prose.", []))

    inv, _ = run(model)

    assert inv.status == "done"
    assert inv.report is None
    assert inv.raw_answer == "Still prose."


def test_out_of_steps_forces_a_final_answer_without_tools():
    model = ScriptedModel(call(query="a"), call(query="b"), Reply(REPORT, []))

    inv, used = run(model, max_steps=2)

    assert used == ["a", "b"]
    assert inv.report is not None
    last_messages, last_tools = model.calls[-1]
    assert last_messages[-1] == {"role": "user", "content": OUT_OF_STEPS}
    assert last_tools is None


def test_model_failure_marks_the_investigation_failed():
    model = ScriptedModel(ModelError("could not reach the model server: refused"))

    inv, _ = run(model)

    assert inv.status == "failed"
    assert inv.error == "could not reach the model server: refused"
    assert inv.finished_at == NOW


def test_timeout_marks_the_investigation_failed():
    class Slow(ScriptedModel):
        async def chat(self, messages, tools=None):
            await asyncio.sleep(5)

    inv, _ = run(Slow(), timeout=0.05)

    assert inv.status == "failed"
    assert inv.error == "timed out after 0 s"


def test_incident_description():
    text = describe_incident(incident(), NOW)
    assert text.startswith("Current time (UTC): 2026-10-22 23:30:00")
    assert "firing since 23:15:05 UTC" in text
    assert "summary: payments skipped invalid messages" in text


def test_initial_findings_come_first_and_are_marked_automatic():
    used = []
    model = ScriptedModel(Reply(REPORT, []))
    investigator = Investigator(make_tools(used), 6000, 8, 30, lambda: NOW)
    inv = Investigation(id=1, incident_id=1, model="scripted")

    asyncio.run(investigator.run(incident(), model, inv))

    # The alert is about job=payments, so its WARN/ERROR logs are fetched up front.
    assert [(s.tool, s.auto) for s in inv.steps] == [("loki_logs", True)]
    first_message = model.calls[0][0][1]["content"]
    assert "Initial findings (collected automatically" in first_message
    assert "WARN skipping invalid order.created message" in first_message
