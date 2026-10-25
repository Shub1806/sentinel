from fastapi.testclient import TestClient

from app.config import Settings
from app.incidents import IncidentStore
from app.investigator import Investigator
from app.llm import Reply
from app.main import create_app
from app.records import Step
from app.report import Report
from app.runner import InvestigationRunner
from tests.payloads import alert, notification

REPORT = (
    '{"summary": "payments crash-loops", "likely_cause": "MAX_AMOUNT_CENTS=abc", '
    '"evidence": ["ValueError: abc"], '
    '"suggested_action": {"kind": "rollout_undo", "target": "payments"}, "confidence": "high"}'
)


class FixedModel:
    model = "m"

    async def chat(self, messages, tools=None):
        return Reply(REPORT, [])


def make(model="small-model"):
    store = IncidentStore()
    runner = InvestigationRunner(
        Investigator({}, 6000, 4, 30, triage=False), lambda m: FixedModel()
    )
    app = create_app(store, Settings(llm_model=model, alertmanager_url=""), runner)
    return TestClient(app), store, runner


def test_empty_list():
    client, _, _ = make()
    with client:
        page = client.get("/")
    assert page.status_code == 200
    assert "No incidents." in page.text
    assert "model: small-model" in page.text


def test_list_and_detail_show_the_report():
    client, store, runner = make()
    with client:
        client.post("/alerts", json=notification(alert("payments")))
        client.portal.call(runner.wait)
        listing = client.get("/").text
        detail = client.get("/ui/incidents/1").text

    assert 'href="/ui/incidents/1"' in listing
    assert "MAX_AMOUNT_CENTS=abc" in listing
    assert "payments crash-loops" in detail
    assert "rollout_undo" in detail and "<code>payments</code>" in detail
    assert "ValueError: abc" in detail
    assert 'http-equiv="refresh"' not in detail  # nothing running any more


def test_page_refreshes_while_an_investigation_runs():
    client, store, _ = make(model="")
    with client:
        client.post("/alerts", json=notification(alert("payments")))
        inv = store.new_investigation(store.get(1), "m")
        inv.status = "running"
        assert 'http-equiv="refresh"' in client.get("/ui/incidents/1").text
        assert 'http-equiv="refresh"' in client.get("/").text


def test_text_from_alerts_and_models_is_escaped():
    client, store, _ = make(model="")
    body = notification(alert("payments"))
    body["alerts"][0]["annotations"]["summary"] = "<script>alert(1)</script>"
    with client:
        client.post("/alerts", json=body)
        inv = store.new_investigation(store.get(1), "m")
        inv.status = "done"
        inv.steps.append(Step("loki_logs", {"query": "<b>"}, "<img src=x>", 0.1))
        inv.report = Report(summary="<i>x</i>", evidence=["<b>y</b>"])
        page = client.get("/ui/incidents/1").text

    assert "<script>" not in page and "&lt;script&gt;" in page
    assert "<img src=x>" not in page and "<i>x</i>" not in page and "<b>y</b>" not in page


def test_investigate_button_starts_a_run_and_redirects():
    client, store, runner = make()
    with client:
        client.post("/alerts", json=notification(alert("payments")))
        client.portal.call(runner.wait)
        response = client.post(
            "/ui/incidents/1/investigate",
            content="model=other-model",
            headers={"content-type": "application/x-www-form-urlencoded"},
            follow_redirects=False,
        )
        client.portal.call(runner.wait)

    assert response.status_code == 303
    assert response.headers["location"] == "/ui/incidents/1"
    assert [i.model for i in store.get(1).investigations] == ["small-model", "other-model"]


def test_unknown_incident_page_is_404():
    client, _, _ = make()
    with client:
        assert client.get("/ui/incidents/9").status_code == 404
