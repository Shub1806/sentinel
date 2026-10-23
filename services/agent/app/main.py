"""FastAPI app: receives Alertmanager webhooks, keeps track of incidents, investigates them."""

import logging
from contextlib import asynccontextmanager
from dataclasses import asdict

import httpx2
from fastapi import FastAPI, HTTPException, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from app.alerts import Notification
from app.catchup import firing_notifications
from app.config import Settings, load_settings
from app.incidents import Incident, IncidentStore
from app.investigator import Investigation, Investigator
from app.llm import ChatModel
from app.logging_setup import configure_logging
from app.metrics import INCIDENTS_OPENED, NOTIFICATIONS_RECEIVED, OPEN_INCIDENTS
from app.runner import InvestigationRunner
from app.tools.registry import build_tools

log = logging.getLogger("agent")


def _summary(incident: Incident) -> dict:
    latest = incident.investigations[-1] if incident.investigations else None
    return {
        "id": incident.id,
        "title": incident.title,
        "status": incident.status,
        "opened_at": incident.opened_at,
        "resolved_at": incident.resolved_at,
        "firing_alerts": incident.firing_alerts,
        "alerts": len(incident.alerts),
        "notifications": incident.notifications,
        "investigation": latest.status if latest else None,
    }


def _investigation(inv: Investigation) -> dict:
    seconds = None
    if inv.started_at and inv.finished_at:
        seconds = round((inv.finished_at - inv.started_at).total_seconds(), 1)
    return {
        "id": inv.id,
        "model": inv.model,
        "status": inv.status,
        "started_at": inv.started_at,
        "finished_at": inv.finished_at,
        "seconds": seconds,
        "tokens": inv.tokens,
        "error": inv.error,
        "steps": [
            {
                "tool": s.tool,
                "arguments": s.arguments,
                "seconds": s.seconds,
                "auto": s.auto,
                "result": s.result[:500],
            }
            for s in inv.steps
        ],
        "report": inv.report.model_dump() if inv.report else None,
        # Shown only when the model's answer could not be read as a report.
        "raw_answer": "" if inv.report else inv.raw_answer[:2000],
    }


def build_runner(settings: Settings, tool_client, llm_client) -> InvestigationRunner:
    investigator = Investigator(
        build_tools(settings, tool_client),
        settings.tool_max_chars,
        settings.max_steps,
        settings.investigation_timeout_seconds,
        namespace=settings.namespace,
    )
    return InvestigationRunner(
        investigator,
        lambda model: ChatModel(llm_client, settings.llm_base_url, model, settings.llm_api_key),
    )


def handle_notification(app: FastAPI, notification: Notification, source: str) -> Incident:
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
            "source": source,
        },
    )
    model = app.state.settings.llm_model
    if opened and incident.status == "firing" and model:
        app.state.runner.start(incident, model)
    return incident


async def catch_up(app: FastAPI, client) -> None:
    """Opens incidents for alerts that were already firing when the agent started."""
    url = app.state.settings.alertmanager_url
    if not url:
        return
    try:
        notifications = await firing_notifications(client, url)
    except (httpx2.HTTPError, ValueError, KeyError) as exc:
        log.warning("could not read firing alerts from Alertmanager", extra={"error": str(exc)})
        return
    for notification in notifications:
        handle_notification(app, notification, source="catch-up")


def create_app(
    store: IncidentStore | None = None,
    settings: Settings | None = None,
    runner: InvestigationRunner | None = None,
    catch_up_client=None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        configure_logging()
        app.state.settings = settings or load_settings()
        clients = []
        if runner is None:
            s = app.state.settings
            tool_client = httpx2.AsyncClient(timeout=s.tool_timeout_seconds)
            llm_client = httpx2.AsyncClient(timeout=s.llm_timeout_seconds)
            clients = [tool_client, llm_client]
            app.state.runner = build_runner(s, tool_client, llm_client)
        else:
            app.state.runner = runner
        log.info("started", extra={"model": app.state.settings.llm_model or "(off)"})
        if catch_up_client is not None:
            await catch_up(app, catch_up_client)
        elif clients:
            await catch_up(app, clients[0])
        yield
        app.state.runner.cancel()
        for client in clients:
            await client.aclose()

    app = FastAPI(title="agent", lifespan=lifespan)
    app.state.store = store or IncidentStore()

    @app.post("/alerts")
    async def receive_alerts(notification: Notification) -> dict:
        """Alertmanager webhook. A 4xx/5xx makes Alertmanager log an error and retry."""
        incident = handle_notification(app, notification, source="webhook")
        return {"incident_id": incident.id, "status": incident.status}

    @app.get("/incidents")
    async def list_incidents() -> list[dict]:
        return [_summary(i) for i in app.state.store.all()]

    def _incident_or_404(incident_id: int) -> Incident:
        incident = app.state.store.get(incident_id)
        if incident is None:
            raise HTTPException(status_code=404, detail="incident not found")
        return incident

    @app.get("/incidents/{incident_id}")
    async def get_incident(incident_id: int) -> dict:
        incident = _incident_or_404(incident_id)
        return {
            **_summary(incident),
            "alerts": [asdict(a) for a in incident.alerts.values()],
            "investigations": [_investigation(i) for i in incident.investigations],
        }

    @app.post("/incidents/{incident_id}/investigate", status_code=202)
    async def investigate(incident_id: int, model: str | None = None) -> dict:
        """Investigate (again), optionally with another model, e.g. ?model=llama3.2:3b."""
        incident = _incident_or_404(incident_id)
        chosen = model or app.state.settings.llm_model
        if not chosen:
            raise HTTPException(status_code=409, detail="no model configured (set LLM_MODEL)")
        inv = app.state.runner.start(incident, chosen)
        return {"incident_id": incident.id, "investigation_id": inv.id, "model": chosen}

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
