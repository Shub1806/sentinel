"""The agent's web page: incidents, what the agent found, and (later) the action buttons.

Server-rendered HTML, no JavaScript. Open it through the tunnel:
kubectl -n agent port-forward svc/agent 18003:8003, then http://localhost:18003/
"""

import asyncio
import logging
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.actions import EXECUTABLE, ActionError, why_not
from app.metrics import ACTIONS

log = logging.getLogger("agent.actions")
router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")


def _time(value: datetime | None) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S") if value else "-"


def _duration(start: datetime | None, end: datetime | None) -> str:
    if not start:
        return "-"
    seconds = int(((end or datetime.now(UTC)) - start).total_seconds())
    if seconds < 120:
        return f"{seconds}s"
    if seconds < 7200:
        return f"{seconds // 60}m"
    return f"{seconds // 3600}h{seconds % 3600 // 60:02d}m"


templates.env.filters["utc"] = _time
templates.env.globals["duration"] = _duration


def _busy(incidents) -> bool:
    return (
        any(i.status in ("queued", "running") for inc in incidents for i in inc.investigations)
        or any(a.status == "running" for inc in incidents for a in inc.actions)
        or any(
            inc.postmortem and inc.postmortem.status in ("queued", "writing") for inc in incidents
        )
    )


def _decision_for(incident, inv):
    return next((a for a in incident.actions if a.investigation_id == inv.id), None)


def _latest_report(incident):
    """The newest investigation that produced a report, else the newest one."""
    for inv in reversed(incident.investigations):
        if inv.report:
            return inv
    return incident.investigations[-1] if incident.investigations else None


@router.get("/", response_class=HTMLResponse)
async def incident_list(request: Request):
    incidents = request.app.state.store.all()
    return templates.TemplateResponse(
        request,
        "incidents.html",
        {
            "incidents": incidents,
            "latest": {i.id: _latest_report(i) for i in incidents},
            "refresh": _busy(incidents),
            "settings": request.app.state.settings,
        },
    )


@router.get("/ui/incidents/{incident_id}", response_class=HTMLResponse)
async def incident_page(request: Request, incident_id: int):
    incident = request.app.state.store.get(incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="incident not found")
    shown = _latest_report(incident)
    blocked = None
    if shown and shown.report:
        a = shown.report.suggested_action
        blocked = why_not(a.kind, a.target, a.replicas, request.app.state.settings.action_targets)
    return templates.TemplateResponse(
        request,
        "incident.html",
        {
            "incident": incident,
            "shown": shown,
            "decision": _decision_for(incident, shown) if shown else None,
            "blocked": blocked,
            "executable": EXECUTABLE,
            "refresh": _busy([incident]),
            "settings": request.app.state.settings,
        },
    )


@router.post("/ui/incidents/{incident_id}/investigate")
async def investigate_from_page(request: Request, incident_id: int):
    incident = request.app.state.store.get(incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="incident not found")
    form = parse_qs((await request.body()).decode())
    model = (form.get("model", [""])[0] or request.app.state.settings.llm_model).strip()
    if not model:
        raise HTTPException(status_code=409, detail="no model configured (set LLM_MODEL)")
    request.app.state.runner.start(incident, model)
    return RedirectResponse(f"/ui/incidents/{incident_id}", status_code=303)


async def _carry_out(app, action) -> None:
    store = app.state.store
    try:
        action.result = await app.state.executor.run(action.kind, action.target, action.replicas)
        action.status = "done"
    except ActionError as exc:
        action.status, action.result = "failed", str(exc)
    action.finished_at = datetime.now(UTC)
    store.save_action(action)
    ACTIONS.labels(kind=action.kind, status=action.status).inc()
    log.info(
        "action finished",
        extra={"action_id": action.id, "status": action.status, "result": action.result},
    )


@router.post("/ui/incidents/{incident_id}/actions")
async def decide(request: Request, incident_id: int):
    """Approve or reject the action suggested by one investigation. Each can be decided once."""
    app = request.app
    incident = app.state.store.get(incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="incident not found")
    form = parse_qs((await request.body()).decode())
    decision = form.get("decision", [""])[0]
    try:
        investigation_id = int(form.get("investigation", ["0"])[0])
    except ValueError:
        investigation_id = 0
    inv = next((i for i in incident.investigations if i.id == investigation_id), None)
    if inv is None or inv.report is None:
        raise HTTPException(status_code=400, detail="no report to act on")
    if decision not in ("approve", "reject"):
        raise HTTPException(status_code=400, detail="decision must be approve or reject")
    if _decision_for(incident, inv):
        raise HTTPException(status_code=409, detail="this suggestion was already decided")
    suggested = inv.report.suggested_action
    if suggested.kind not in EXECUTABLE:
        raise HTTPException(status_code=400, detail="nothing to carry out for this suggestion")
    reason = why_not(
        suggested.kind, suggested.target, suggested.replicas, app.state.settings.action_targets
    )
    if decision == "approve" and reason:
        raise HTTPException(status_code=400, detail=reason)

    who = f"page ({request.client.host if request.client else 'unknown'})"
    now = datetime.now(UTC)
    if decision == "reject":
        action = app.state.store.new_action(
            incident, inv, status="rejected", decided_by=who, decided_at=now, finished_at=now
        )
        ACTIONS.labels(kind=action.kind, status="rejected").inc()
    else:
        action = app.state.store.new_action(
            incident, inv, status="running", decided_by=who, decided_at=now
        )
        task = asyncio.create_task(_carry_out(app, action))
        app.state.action_tasks.add(task)
        task.add_done_callback(app.state.action_tasks.discard)
    log.info(
        "action " + ("approved" if decision == "approve" else "rejected"),
        extra={
            "action_id": action.id,
            "incident_id": incident.id,
            "kind": action.kind,
            "target": action.target,
            "replicas": action.replicas,
            "decided_by": who,
        },
    )
    return RedirectResponse(f"/ui/incidents/{incident_id}", status_code=303)


@router.post("/ui/incidents/{incident_id}/postmortem")
async def postmortem_again(request: Request, incident_id: int):
    """Writes the post-mortem again (for example after another investigation or action)."""
    incident = request.app.state.store.get(incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="incident not found")
    if incident.status != "resolved":
        raise HTTPException(status_code=409, detail="the incident is still firing")
    pm = incident.postmortem
    if pm and pm.status in ("queued", "writing"):
        raise HTTPException(status_code=409, detail="a post-mortem is already being written")
    request.app.state.runner.start_postmortem(incident, request.app.state.settings.llm_model)
    return RedirectResponse(f"/ui/incidents/{incident_id}", status_code=303)
