"""The agent's web page: incidents, what the agent found, and (later) the action buttons.

Server-rendered HTML, no JavaScript. Open it through the tunnel:
kubectl -n agent port-forward svc/agent 18003:8003, then http://localhost:18003/
"""

from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

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
    return any(i.status in ("queued", "running") for inc in incidents for i in inc.investigations)


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
    return templates.TemplateResponse(
        request,
        "incident.html",
        {
            "incident": incident,
            "shown": _latest_report(incident),
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
