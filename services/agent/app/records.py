"""Investigation records, shared by the investigator, the store and the web page."""

from dataclasses import dataclass, field
from datetime import datetime

from app.report import Report


@dataclass
class Step:
    tool: str
    arguments: dict
    result: str
    seconds: float
    auto: bool = False  # collected by the initial findings, not chosen by the model


@dataclass
class Investigation:
    id: int
    incident_id: int
    model: str
    status: str = "queued"  # queued, running, done, failed
    started_at: datetime | None = None
    finished_at: datetime | None = None
    steps: list[Step] = field(default_factory=list)
    report: Report | None = None
    raw_answer: str = ""
    error: str = ""
    tokens: int = 0


@dataclass
class Action:
    """A suggested action and what a person decided about it."""

    id: int
    incident_id: int
    investigation_id: int
    kind: str
    target: str
    replicas: int | None = None
    status: str = "running"  # rejected, running, done, failed
    decided_by: str = ""
    decided_at: datetime | None = None
    finished_at: datetime | None = None
    result: str = ""


@dataclass
class Postmortem:
    """Written when an incident resolves: facts from the record, plus an optional narrative."""

    incident_id: int
    status: str = "queued"  # queued, writing, done
    model: str = ""  # empty = no narrative (no model, or the model failed)
    generated_at: datetime | None = None
    what_happened: str = ""
    impact: str = ""
    follow_ups: list[str] = field(default_factory=list)
    markdown: str = ""
    error: str = ""
