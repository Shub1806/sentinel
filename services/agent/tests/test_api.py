import json
import logging

import httpx2
from fastapi.testclient import TestClient
from prometheus_client import REGISTRY

from app.config import Settings
from app.incidents import IncidentStore
from app.investigator import Investigator
from app.llm import Reply
from app.main import create_app
from app.runner import InvestigationRunner
from tests.payloads import alert, notification


def make_client() -> TestClient:
    # No model and no Alertmanager: only the webhook and the incident list are used.
    return TestClient(create_app(IncidentStore(), Settings(alertmanager_url="")))


def metric(name: str, **labels: str) -> float:
    """Metrics are shared by all tests, so tests compare before/after values."""
    return REGISTRY.get_sample_value(name, labels) or 0.0


def test_health_endpoints():
    with make_client() as client:
        assert client.get("/healthz").status_code == 200
        assert client.get("/readyz").status_code == 200


def test_webhook_opens_an_incident_and_lists_it():
    with make_client() as client:
        response = client.post("/alerts", json=notification(alert("payments")))
        assert response.status_code == 200
        assert response.json() == {"incident_id": 1, "status": "firing"}

        incidents = client.get("/incidents").json()
        assert len(incidents) == 1
        assert incidents[0]["title"] == "InvalidMessagesReceived"
        assert incidents[0]["firing_alerts"] == 1

        detail = client.get("/incidents/1").json()
        assert detail["alerts"][0]["labels"]["job"] == "payments"
        assert detail["alerts"][0]["annotations"]["summary"] == "payments skipped invalid messages"


def test_unknown_incident_is_404():
    with make_client() as client:
        assert client.get("/incidents/99").status_code == 404


def test_bad_body_is_rejected():
    with make_client() as client:
        assert client.post("/alerts", json={"hello": "world"}).status_code == 422
        empty = notification(alert("payments"))
        empty["alerts"] = []
        assert client.post("/alerts", json=empty).status_code == 422


def test_metrics_count_notifications_and_incidents():
    firing = metric("agent_notifications_received_total", status="firing")
    resolved = metric("agent_notifications_received_total", status="resolved")
    opened = metric("agent_incidents_opened_total")

    with make_client() as client:
        client.post("/alerts", json=notification(alert("payments")))
        assert metric("agent_open_incidents") == 1
        client.post(
            "/alerts", json=notification(alert("payments", "resolved", "2026-10-22T23:25:00Z"))
        )
        body = client.get("/metrics").text

    assert metric("agent_notifications_received_total", status="firing") == firing + 1
    assert metric("agent_notifications_received_total", status="resolved") == resolved + 1
    assert metric("agent_incidents_opened_total") == opened + 1
    assert metric("agent_open_incidents") == 0
    assert 'agent_notifications_received_total{status="resolved"}' in body


def test_each_notification_is_logged_as_json(capsys):
    with make_client() as client:
        client.post("/alerts", json=notification(alert("payments")))
    try:
        lines = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line]
    finally:
        logging.getLogger().handlers = []
    opened = [entry for entry in lines if entry["msg"] == "incident opened"]
    assert len(opened) == 1
    assert opened[0]["incident_id"] == 1
    assert opened[0]["alerts"] == ["InvalidMessagesReceived:firing"]


# ---------- Investigations through the API ----------


REPORT = (
    '{"summary": "s", "likely_cause": "c", "evidence": [], '
    '"suggested_action": {"kind": "none"}, "confidence": "high"}'
)


class FixedModel:
    def __init__(self, model):
        self.model = model

    async def chat(self, messages, tools=None):
        return Reply(REPORT, [])


def client_with_model(model_setting: str):
    used_models = []

    def factory(name):
        used_models.append(name)
        return FixedModel(name)

    runner = InvestigationRunner(Investigator({}, 6000, 4, 30), factory)
    app = create_app(
        IncidentStore(), Settings(llm_model=model_setting, alertmanager_url=""), runner
    )
    return TestClient(app), runner, used_models


def test_new_incident_is_investigated_with_the_configured_model():
    client, runner, used = client_with_model("small-model")
    with client:
        client.post("/alerts", json=notification(alert("payments")))
        client.portal.call(runner.wait)
        detail = client.get("/incidents/1").json()

    assert used == ["small-model"]
    inv = detail["investigations"][0]
    assert (inv["status"], inv["model"], inv["report"]["likely_cause"]) == (
        "done",
        "small-model",
        "c",
    )
    assert detail["investigation"] == "done"


def test_no_model_means_no_investigation():
    client, runner, used = client_with_model("")
    with client:
        client.post("/alerts", json=notification(alert("payments")))
        assert client.post("/incidents/1/investigate").status_code == 409
        assert client.get("/incidents/1").json()["investigations"] == []
    assert used == []


def test_investigate_again_with_another_model():
    client, runner, used = client_with_model("small-model")
    with client:
        client.post("/alerts", json=notification(alert("payments")))
        response = client.post("/incidents/1/investigate", params={"model": "other-model"})
        client.portal.call(runner.wait)
        detail = client.get("/incidents/1").json()

    assert response.status_code == 202
    assert response.json()["investigation_id"] == 2
    assert [i["model"] for i in detail["investigations"]] == ["small-model", "other-model"]


def test_updates_and_resolutions_do_not_start_investigations():
    client, runner, used = client_with_model("small-model")
    with client:
        client.post("/alerts", json=notification(alert("payments")))
        client.post("/alerts", json=notification(alert("payments"), alert("notifications")))
        client.post(
            "/alerts", json=notification(alert("payments", "resolved", "2026-10-22T23:25:00Z"))
        )
        client.portal.call(runner.wait)
    assert used == ["small-model"]


def test_alerts_already_firing_are_picked_up_on_startup():
    def alertmanager(request):
        assert request.url.path == "/api/v2/alerts"
        firing = notification(alert("payments"))["alerts"][0]
        return httpx2.Response(200, json=[{**firing, "receivers": [{"name": "agent"}]}])

    am_client = httpx2.AsyncClient(transport=httpx2.MockTransport(alertmanager))
    used = []
    runner = InvestigationRunner(
        Investigator({}, 6000, 4, 30, triage=False), lambda m: used.append(m) or FixedModel(m)
    )
    settings = Settings(llm_model="small-model", alertmanager_url="http://am:9093")
    app = create_app(IncidentStore(), settings, runner, catch_up_client=am_client)
    with TestClient(app) as client:
        client.portal.call(runner.wait)
        incidents = client.get("/incidents").json()
        # The webhook for the same group later updates that incident, not a new one.
        client.post("/alerts", json=notification(alert("payments"), alert("notifications")))
        after = client.get("/incidents").json()

    assert [(i["title"], i["investigation"]) for i in incidents] == [
        ("InvalidMessagesReceived", "done")
    ]
    assert used == ["small-model"]
    assert len(after) == 1 and after[0]["firing_alerts"] == 2
