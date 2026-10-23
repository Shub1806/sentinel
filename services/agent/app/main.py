"""FastAPI app: receives Alertmanager webhooks and keeps track of incidents."""

import logging
from contextlib import asynccontextmanager
from dataclasses import asdict

from fastapi import FastAPI, HTTPException, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from app.alerts import Notification
from app.incidents import Incident, IncidentStore
from app.logging_setup import configure_logging
from app.metrics import INCIDENTS_OPENED, NOTIFICATIONS_RECEIVED, OPEN_INCIDENTS

log = logging.getLogger("agent")


def _summary(incident: Incident) -> dict:
    return {
        "id": incident.id,
        "title": incident.title,
        "status": incident.status,
        "opened_at": incident.opened_at,
        "resolved_at": incident.resolved_at,
        "firing_alerts": incident.firing_alerts,
        "alerts": len(incident.alerts),
        "notifications": incident.notifications,
    }


def create_app(store: IncidentStore | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        configure_logging()
        yield

    app = FastAPI(title="agent", lifespan=lifespan)
    app.state.store = store or IncidentStore()

    @app.post("/alerts")
    async def receive_alerts(notification: Notification) -> dict:
        """Alertmanager webhook. A 4xx/5xx makes Alertmanager log an error and retry."""
        incident, opened = app.state.store.record(notification)
        NOTIFICATIONS_RECEIVED.labels(status=notification.status).inc()
        if opened:
            INCIDENTS_OPENED.inc()
        OPEN_INCIDENTS.set(app.state.store.open_count())
        log.info(
            "incident opened" if opened else "incident updated",
            extra={
                "incident_id": incident.id,
                "title": incident.title,
                "incident_status": incident.status,
                "notification_status": notification.status,
                "alerts": [f"{a.name}:{a.status}" for a in notification.alerts],
            },
        )
        return {"incident_id": incident.id, "status": incident.status}

    @app.get("/incidents")
    async def list_incidents() -> list[dict]:
        return [_summary(i) for i in app.state.store.all()]

    @app.get("/incidents/{incident_id}")
    async def get_incident(incident_id: int) -> dict:
        incident = app.state.store.get(incident_id)
        if incident is None:
            raise HTTPException(status_code=404, detail="incident not found")
        return {**_summary(incident), "alerts": [asdict(a) for a in incident.alerts.values()]}

    @app.get("/healthz")
    async def healthz() -> dict:
        return {"status": "ok"}

    @app.get("/readyz")
    async def readyz() -> dict:
        return {"status": "ready"}

    @app.get("/metrics")
    async def metrics() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    return app


app = create_app()
