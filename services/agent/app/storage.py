"""SQLite storage for incidents and investigations, so a restart doesn't lose them.

Each row holds one object as JSON: the agent keeps everything in memory while it runs and
writes every change through to the file. Small data (a few hundred incidents at most), one
writer (this process), so this stays simple.
"""

import json
import sqlite3
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from app.incidents import AlertState, Incident
from app.records import Investigation, Step
from app.report import Report

SCHEMA = """
CREATE TABLE IF NOT EXISTS incidents (
    id           INTEGER PRIMARY KEY,
    incident_key TEXT NOT NULL,
    status       TEXT NOT NULL,
    data         TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS investigations (
    id          INTEGER PRIMARY KEY,
    incident_id INTEGER NOT NULL REFERENCES incidents(id),
    data        TEXT NOT NULL
);
"""


def _time(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def incident_to_json(incident: Incident) -> str:
    data = asdict(incident)
    data.pop("investigations")  # stored in their own table
    return json.dumps(data, default=str)


def incident_from_json(text: str) -> Incident:
    data = json.loads(text)
    alerts = {
        fp: AlertState(**{**a, "starts_at": _time(a["starts_at"]), "ends_at": _time(a["ends_at"])})
        for fp, a in data.pop("alerts").items()
    }
    return Incident(
        **{
            **data,
            "opened_at": _time(data["opened_at"]),
            "updated_at": _time(data["updated_at"]),
            "resolved_at": _time(data["resolved_at"]),
            "alerts": alerts,
        }
    )


def investigation_to_json(inv: Investigation) -> str:
    data = asdict(inv)
    data["report"] = inv.report.model_dump() if inv.report else None
    return json.dumps(data, default=str)


def investigation_from_json(text: str) -> Investigation:
    data = json.loads(text)
    return Investigation(
        **{
            **data,
            "started_at": _time(data["started_at"]),
            "finished_at": _time(data["finished_at"]),
            "steps": [Step(**s) for s in data["steps"]],
            "report": Report.model_validate(data["report"]) if data["report"] else None,
        }
    )


class Database:
    def __init__(self, path: str):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)

    def save_incident(self, incident: Incident) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO incidents (id, incident_key, status, data) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET status = excluded.status, data = excluded.data",
                (incident.id, incident.group_key, incident.status, incident_to_json(incident)),
            )

    def save_investigation(self, inv: Investigation) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO investigations (id, incident_id, data) VALUES (?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET data = excluded.data",
                (inv.id, inv.incident_id, investigation_to_json(inv)),
            )

    def load(self) -> list[Incident]:
        """All incidents, each with its investigations (oldest first)."""
        incidents = {
            row[0]: incident_from_json(row[1])
            for row in self.conn.execute("SELECT id, data FROM incidents ORDER BY id")
        }
        for (data,) in self.conn.execute("SELECT data FROM investigations ORDER BY id"):
            inv = investigation_from_json(data)
            if inv.incident_id in incidents:
                incidents[inv.incident_id].investigations.append(inv)
        return list(incidents.values())

    def close(self) -> None:
        self.conn.close()
