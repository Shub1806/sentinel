import asyncio
import json
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from app import postmortem
from app.alerts import Notification
from app.config import Settings
from app.incidents import IncidentStore
from app.investigator import Investigator
from app.llm import ModelError, Reply
from app.main import create_app
from app.records import Postmortem
from app.report import Report, SuggestedAction
from app.runner import InvestigationRunner
from app.storage import Database
from tests.payloads import alert, notification

T0 = datetime(2026, 10, 25, 3, 20, tzinfo=UTC)
NARRATIVE = json.dumps(
    {
        "what_happened": "A bad setting broke payments; the agent found it; the rollout "
        "was undone.",
        "impact": "Payments stopped for 4 minutes; orders were still accepted.",
        "follow_ups": ["Validate settings before rollout", "b", "c", "d", "e"],
    }
)


class Clock:
    def __init__(self):
        self.now = T0

    def __call__(self):
        return self.now


def resolved_incident():
    """Alert fired 03:19:30, opened 03:20, diagnosed 03:21, fixed 03:24:19, resolved 03:30."""
    clock = Clock()
    store = IncidentStore(clock=clock)
    body = notification(alert("payments"))
    body["alerts"][0]["startsAt"] = "2026-10-25T03:19:30Z"
    incident, _ = store.record(Notification.model_validate(body))
    inv = store.new_investigation(incident, "qwen3:4b")
    inv.status, inv.started_at, inv.finished_at = "done", T0, T0 + timedelta(minutes=1)
    inv.report = Report(
        summary="payments crash-loops",
        likely_cause="MAX_AMOUNT_CENTS=abc in the latest rollout",
        evidence=["ValueError: 'abc'"],
        suggested_action=SuggestedAction(kind="rollout_undo", target="payments"),
        confidence="high",
    )
    store.new_action(
        incident,
        inv,
        status="done",
        decided_by="page (10.42.0.1)",
        decided_at=T0 + timedelta(minutes=4, seconds=14),
        finished_at=T0 + timedelta(minutes=4, seconds=19),
        result="rolled payments back from revision 2 to 1; rolled out",
    )
    clock.now = T0 + timedelta(minutes=10)
    resolved = notification(alert("payments", "resolved", "2026-10-25T03:29:30Z"))
    resolved["alerts"][0]["startsAt"] = "2026-10-25T03:19:30Z"  # same alert, same start
    store.record(Notification.model_validate(resolved))
    return store, incident


def test_timeline_is_in_order_and_covers_every_step():
    _, incident = resolved_incident()

    lines = [text for _, text in postmortem.timeline(incident)]

    assert lines == [
        "alert InvalidMessagesReceived fired (payments)",
        "incident opened",
        "investigation 1 started (qwen3:4b)",
        "investigation 1 reported: MAX_AMOUNT_CENTS=abc in the latest rollout "
        "(confidence high; suggested rollout_undo payments)",
        "rollout_undo payments approved by page (10.42.0.1)",
        "rollout_undo payments done: rolled payments back from revision 2 to 1; rolled out",
        "alert InvalidMessagesReceived cleared (payments)",
        "incident resolved",
    ]


def test_durations():
    _, incident = resolved_incident()
    assert postmortem.durations(incident) == {
        "alert fired → incident opened": "30 s",
        "incident opened → diagnosis": "60 s",
        "incident opened → fix carried out": "4 min 19 s",
        "alert fired → resolved": "10 min 30 s",
    }


class Model:
    model = "qwen3:4b"

    def __init__(self, reply):
        self.reply = reply
        self.sent = None

    async def chat(self, messages, tools=None):
        self.sent = messages
        if isinstance(self.reply, Exception):
            raise self.reply
        return Reply(self.reply, [])


def write(incident, model):
    pm = Postmortem(incident_id=incident.id)
    return asyncio.run(postmortem.write(incident, pm, model, lambda: T0 + timedelta(hours=1)))


def test_with_a_narrative():
    _, incident = resolved_incident()
    model = Model("Here you go: " + NARRATIVE)

    pm = write(incident, model)

    assert pm.status == "done" and pm.model == "qwen3:4b" and pm.error == ""
    assert len(pm.follow_ups) == 4  # capped
    md = pm.markdown
    assert md.startswith("# Post-mortem: #1 InvalidMessagesReceived")
    assert "## What happened\n\nA bad setting broke payments" in md
    assert "## Impact" in md and "- [ ] Validate settings before rollout" in md
    assert "- 2026-10-25 03:24:19 rollout_undo payments done" in md
    assert "incident opened → fix carried out: 4 min 19 s" in md
    # The model only got the recorded facts.
    assert "Timeline (UTC):" in model.sent[1]["content"]
    assert "Evidence: ValueError: 'abc'" in model.sent[1]["content"]


def test_facts_only_without_a_model_or_with_a_bad_answer():
    _, incident = resolved_incident()

    no_model = write(incident, None)
    bad = write(incident, Model("I think it went well."))
    down = write(incident, Model(ModelError("could not reach the model server")))

    for pm in (no_model, bad, down):
        assert pm.status == "done"
        assert "## Timeline (UTC)" in pm.markdown and "## What happened" not in pm.markdown
    assert "(facts only, no model narrative)" in no_model.markdown
    assert bad.error == "the model's narrative was not valid JSON; facts only"
    assert down.error == "no narrative: could not reach the model server"


def test_no_report_and_no_action_still_renders():
    store = IncidentStore()
    incident, _ = store.record(Notification.model_validate(notification(alert("payments"))))
    md = write(incident, None).markdown
    assert "No investigation report." in md
    assert "- No action was carried out by the agent." in md
    assert "incident opened → diagnosis: n/a" in md


def test_saved_and_reloaded(tmp_path):
    path = str(tmp_path / "agent.db")
    store = IncidentStore(db=Database(path))
    incident, _ = store.record(Notification.model_validate(notification(alert("payments"))))
    pm = store.new_postmortem(incident)
    asyncio.run(postmortem.write(incident, pm, Model(NARRATIVE), lambda: T0))
    store.save_postmortem(pm)
    other, _ = store.record(Notification.model_validate(notification(alert("orders"), group="X")))
    store.new_postmortem(other).status = "writing"
    store.save_postmortem(other.postmortem)

    reloaded = IncidentStore(db=Database(path))

    first = reloaded.get(1).postmortem
    assert (first.status, first.model, first.generated_at) == ("done", "qwen3:4b", T0)
    assert first.markdown == pm.markdown
    assert reloaded.get(2).postmortem.status == "failed"


class FixedModel:
    def __init__(self, name):
        self.model = name

    async def chat(self, messages, tools=None):
        if tools is None and "post-mortem" in messages[0]["content"]:
            return Reply(NARRATIVE, [])
        return Reply('{"summary": "s", "likely_cause": "c"}', [])


def app_client(model):
    store = IncidentStore()
    runner = InvestigationRunner(Investigator({}, 6000, 4, 30, triage=False), FixedModel)
    app = create_app(store, Settings(llm_model=model, alertmanager_url=""), runner)
    return TestClient(app), store, runner


def test_written_when_the_incident_resolves_and_served_as_markdown():
    client, store, runner = app_client("qwen3:4b")
    with client:
        client.post("/alerts", json=notification(alert("payments")))
        client.portal.call(runner.wait)
        assert client.get("/incidents/1/postmortem.md").status_code == 404
        assert client.post("/ui/incidents/1/postmortem").status_code == 409  # still firing
        client.post(
            "/alerts", json=notification(alert("payments", "resolved", "2026-10-25T03:29:30Z"))
        )
        client.portal.call(runner.wait)
        md = client.get("/incidents/1/postmortem.md")
        page = client.get("/ui/incidents/1").text
        again = client.post("/ui/incidents/1/postmortem", follow_redirects=False)
        client.portal.call(runner.wait)

    assert md.status_code == 200
    assert md.headers["content-type"].startswith("text/markdown")
    assert "## What happened" in md.text and "investigation 1 reported: c" in md.text
    assert "download as Markdown" in page and "write again" in page
    assert again.status_code == 303


def test_facts_only_post_mortem_when_no_model_is_set():
    client, store, runner = app_client("")
    with client:
        client.post("/alerts", json=notification(alert("payments")))
        client.post(
            "/alerts", json=notification(alert("payments", "resolved", "2026-10-25T03:29:30Z"))
        )
        client.portal.call(runner.wait)
        md = client.get("/incidents/1/postmortem.md").text
    assert "(facts only, no model narrative)" in md


def test_resolved_incident_without_one_offers_the_button():
    client, store, runner = app_client("")
    with client:
        client.post("/alerts", json=notification(alert("payments")))
        store.get(1).status = "resolved"  # resolved before post-mortems existed
        page = client.get("/ui/incidents/1").text
        client.post("/ui/incidents/1/postmortem")
        client.portal.call(runner.wait)
        after = client.get("/ui/incidents/1").text
    assert "write post-mortem" in page
    assert "download as Markdown" in after and "write again" in after
