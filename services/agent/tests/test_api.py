import json
import logging

from fastapi.testclient import TestClient
from prometheus_client import REGISTRY

from app.incidents import IncidentStore
from app.main import create_app
from tests.payloads import alert, notification


def make_client() -> TestClient:
    return TestClient(create_app(IncidentStore()))


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
