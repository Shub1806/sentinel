"""Post-mortem: a timeline and durations built from what the agent recorded, plus a short
narrative written by the model (optional: without a model the facts are still complete).
"""

import json
import logging
from datetime import UTC, datetime

from pydantic import BaseModel, Field, ValidationError, field_validator

from app.incidents import Incident
from app.llm import ChatModel, ModelError
from app.records import Postmortem

log = logging.getLogger("agent.postmortem")

NARRATIVE_PROMPT = """You write short, blameless post-mortems for "sentinel", a small order \
system on Kubernetes. You get the incident's timeline, the investigation report and the actions \
taken. Use only these facts. Answer with ONLY this JSON:
{"what_happened": "3-5 plain sentences: the problem, the cause, how it was found and fixed",
 "impact": "1-2 sentences: who or what was affected and for how long",
 "follow_ups": ["concrete improvement to prevent or detect this sooner", "... (at most 4)"]}"""


class Narrative(BaseModel):
    what_happened: str
    impact: str = ""
    follow_ups: list[str] = Field(default_factory=list)

    @field_validator("follow_ups", mode="before")
    @classmethod
    def at_most_four(cls, value):
        if isinstance(value, str):
            value = [value]
        return [str(v) for v in (value or [])][:4]


def _when(value: datetime | None) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S") if value else "-"


def _span(start: datetime | None, end: datetime | None) -> str | None:
    if not start or not end:
        return None
    seconds = max(0, int((end - start).total_seconds()))
    if seconds < 120:
        return f"{seconds} s"
    if seconds < 7200:
        return f"{seconds // 60} min {seconds % 60:02d} s"
    return f"{seconds // 3600} h {seconds % 3600 // 60:02d} min"


def _first_report(incident: Incident):
    return next((i for i in incident.investigations if i.report), None)


def timeline(incident: Incident) -> list[tuple[datetime, str]]:
    events: list[tuple[datetime, str]] = []
    for a in incident.alerts.values():
        where = a.labels.get("pod") or a.labels.get("job") or a.labels.get("redpanda_group") or ""
        events.append((a.starts_at, f"alert {a.name} fired{f' ({where})' if where else ''}"))
        if a.ends_at:
            events.append((a.ends_at, f"alert {a.name} cleared{f' ({where})' if where else ''}"))
    events.append((incident.opened_at, "incident opened"))
    for inv in incident.investigations:
        if inv.started_at:
            events.append((inv.started_at, f"investigation {inv.id} started ({inv.model})"))
        if inv.finished_at:
            if inv.report:
                r = inv.report
                text = (
                    f"investigation {inv.id} reported: {r.likely_cause or r.summary} "
                    f"(confidence {r.confidence}; suggested {r.suggested_action.kind}"
                    f"{' ' + r.suggested_action.target if r.suggested_action.target else ''})"
                )
            else:
                text = f"investigation {inv.id} {inv.status}{': ' + inv.error if inv.error else ''}"
            events.append((inv.finished_at, text))
    for act in incident.actions:
        what = f"{act.kind} {act.target}".strip()
        if act.decided_at:
            verb = "rejected" if act.status == "rejected" else "approved"
            events.append((act.decided_at, f"{what} {verb} by {act.decided_by}"))
        if act.finished_at and act.status != "rejected":
            events.append((act.finished_at, f"{what} {act.status}: {act.result}"))
    if incident.resolved_at:
        events.append((incident.resolved_at, "incident resolved"))
    return sorted(events, key=lambda e: e[0])


def durations(incident: Incident) -> dict[str, str | None]:
    first_alert = min((a.starts_at for a in incident.alerts.values()), default=None)
    reported = next((i.finished_at for i in incident.investigations if i.report), None)
    mitigated = next(
        (a.finished_at for a in incident.actions if a.status == "done" and a.finished_at), None
    )
    return {
        "alert fired → incident opened": _span(first_alert, incident.opened_at),
        "incident opened → diagnosis": _span(incident.opened_at, reported),
        "incident opened → fix carried out": _span(incident.opened_at, mitigated),
        "alert fired → resolved": _span(first_alert, incident.resolved_at),
    }


def facts_text(incident: Incident) -> str:
    """The timeline and the report, as plain text for the model."""
    lines = [f"Incident #{incident.id}: {incident.title}", "Timeline (UTC):"]
    lines += [f"- {_when(t)} {text}" for t, text in timeline(incident)]
    inv = _first_report(incident)
    if inv:
        r = inv.report
        lines += ["", f"Report summary: {r.summary}", f"Likely cause: {r.likely_cause}"]
        lines += [f"Evidence: {e}" for e in r.evidence]
    return "\n".join(lines)


def render(incident: Incident, pm: Postmortem) -> str:
    inv = _first_report(incident)
    out = [
        f"# Post-mortem: #{incident.id} {incident.title}",
        "",
        f"- Status: {incident.status}",
        f"- Opened: {_when(incident.opened_at)} UTC",
        f"- Resolved: {_when(incident.resolved_at)} UTC",
        f"- Written: {_when(pm.generated_at)} UTC"
        + (f" (narrative by {pm.model})" if pm.model else " (facts only, no model narrative)"),
        "",
    ]
    if pm.what_happened:
        out += ["## What happened", "", pm.what_happened, ""]
    if pm.impact:
        out += ["## Impact", "", pm.impact, ""]
    out += ["## Cause", ""]
    if inv:
        r = inv.report
        out += [f"{r.likely_cause or r.summary} (confidence: {r.confidence}, {inv.model})", ""]
        if r.evidence:
            out += ["Evidence:", ""] + [f"- {e}" for e in r.evidence] + [""]
    else:
        out += ["No investigation report.", ""]
    out += ["## What was done", ""]
    if incident.actions:
        for act in incident.actions:
            out.append(
                f"- {act.kind} {act.target}: {act.status}"
                + (f" ({act.result})" if act.result else "")
                + f", decided by {act.decided_by} at {_when(act.decided_at)} UTC"
            )
    else:
        out.append("- No action was carried out by the agent.")
    out += ["", "## Timeline (UTC)", ""]
    out += [f"- {_when(t)} {text}" for t, text in timeline(incident)]
    out += ["", "## Durations", ""]
    out += [f"- {name}: {value or 'n/a'}" for name, value in durations(incident).items()]
    out += [
        "",
        "Times come from the alerts: the problem itself may have started before the first "
        "alert fired.",
    ]
    if pm.follow_ups:
        out += ["", "## Follow-ups", ""] + [f"- [ ] {f}" for f in pm.follow_ups]
    return "\n".join(out) + "\n"


def _parse_narrative(text: str) -> Narrative | None:
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        return Narrative.model_validate(json.loads(text[start : end + 1]))
    except json.JSONDecodeError, ValidationError:
        return None


async def write(incident: Incident, pm: Postmortem, model: ChatModel | None, clock) -> Postmortem:
    pm.status = "writing"
    if model is not None:
        try:
            reply = await model.chat(
                [
                    {"role": "system", "content": NARRATIVE_PROMPT},
                    {"role": "user", "content": facts_text(incident)},
                ]
            )
            narrative = _parse_narrative(reply.content)
            if narrative:
                pm.model = model.model
                pm.what_happened = narrative.what_happened
                pm.impact = narrative.impact
                pm.follow_ups = narrative.follow_ups
            else:
                pm.error = "the model's narrative was not valid JSON; facts only"
        except ModelError as exc:
            pm.error = f"no narrative: {exc}"
    pm.generated_at = clock()
    pm.markdown = render(incident, pm)
    pm.status = "done"
    log.info(
        "post-mortem written",
        extra={"incident_id": incident.id, "model": pm.model, "error": pm.error},
    )
    return pm
