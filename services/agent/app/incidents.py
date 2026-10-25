"""Turns Alertmanager notifications into incidents.

Alertmanager groups alerts (by alert name and namespace, see the route in
infra/helm-values/monitoring.yaml) and sends the whole group again whenever it changes.
Incidents are matched by those group labels (Notification.incident_key).
One group that starts firing opens one incident. The incident is resolved once every
alert in it is resolved. If the same group fires again later, that is a new incident.

Everything is kept in memory and, when a database is given, written through to SQLite
(app/storage.py) and loaded again on startup. Alerts that are still firing are also read
from Alertmanager on startup (app/catchup.py), and match the reloaded incidents.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from itertools import count

from app.alerts import Alert, Notification
from app.records import Action, Investigation


@dataclass
class AlertState:
    fingerprint: str
    name: str
    status: str
    labels: dict[str, str]
    annotations: dict[str, str]
    starts_at: datetime
    ends_at: datetime | None


@dataclass
class Incident:
    id: int
    group_key: str
    title: str
    status: str  # "firing" or "resolved"
    opened_at: datetime
    updated_at: datetime
    resolved_at: datetime | None = None
    notifications: int = 0
    alerts: dict[str, AlertState] = field(default_factory=dict)
    # Investigations of this incident, oldest first.
    investigations: list[Investigation] = field(default_factory=list)
    # Decisions on suggested actions, oldest first.
    actions: list[Action] = field(default_factory=list)

    @property
    def firing_alerts(self) -> int:
        return sum(1 for a in self.alerts.values() if a.status == "firing")


def _state(alert: Alert) -> AlertState:
    ends_at = alert.ends_at if alert.status == "resolved" else None
    return AlertState(
        fingerprint=alert.fingerprint,
        name=alert.name,
        status=alert.status,
        labels=alert.labels,
        annotations=alert.annotations,
        starts_at=alert.starts_at,
        ends_at=ends_at,
    )


class IncidentStore:
    def __init__(self, clock=lambda: datetime.now(UTC), db=None) -> None:
        self._clock = clock
        self._db = db
        self._incidents: dict[int, Incident] = {}
        self._open_by_group: dict[str, int] = {}
        last_incident = last_investigation = last_action = 0
        for incident in db.load() if db else []:
            self._incidents[incident.id] = incident
            if incident.status == "firing":
                self._open_by_group[incident.group_key] = incident.id
            last_incident = max(last_incident, incident.id)
            for inv in incident.investigations:
                last_investigation = max(last_investigation, inv.id)
                if inv.status in ("queued", "running"):
                    # The agent stopped while this was in progress; it will not continue.
                    inv.status, inv.error = "failed", "the agent restarted before it finished"
                    db.save_investigation(inv)
            for action in incident.actions:
                last_action = max(last_action, action.id)
                if action.status == "running":
                    action.status = "failed"
                    action.result = "the agent restarted while the action ran; check the deployment"
                    db.save_action(action)
        self._ids = count(last_incident + 1)
        self._investigation_ids = count(last_investigation + 1)
        self._action_ids = count(last_action + 1)

    def record(self, notification: Notification) -> tuple[Incident, bool]:
        """Applies one notification. Returns the incident and whether it was just opened."""
        now = self._clock()
        incident_id = self._open_by_group.get(notification.incident_key)
        opened = incident_id is None
        if opened:
            incident = Incident(
                id=next(self._ids),
                group_key=notification.incident_key,
                title=notification.title,
                status="firing",
                opened_at=now,
                updated_at=now,
            )
            self._incidents[incident.id] = incident
            self._open_by_group[incident.group_key] = incident.id
        else:
            incident = self._incidents[incident_id]

        for alert in notification.alerts:
            incident.alerts[alert.fingerprint] = _state(alert)
        incident.notifications += 1
        incident.updated_at = now

        if incident.firing_alerts == 0:
            incident.status = "resolved"
            incident.resolved_at = now
            del self._open_by_group[incident.group_key]
        if self._db:
            self._db.save_incident(incident)
        return incident, opened

    def new_investigation(self, incident: Incident, model: str) -> Investigation:
        inv = Investigation(id=next(self._investigation_ids), incident_id=incident.id, model=model)
        incident.investigations.append(inv)
        self.save_investigation(inv)
        return inv

    def save_investigation(self, inv: Investigation) -> None:
        if self._db:
            self._db.save_investigation(inv)

    def new_action(self, incident: Incident, inv: Investigation, **fields) -> Action:
        suggested = inv.report.suggested_action
        action = Action(
            id=next(self._action_ids),
            incident_id=incident.id,
            investigation_id=inv.id,
            kind=suggested.kind,
            target=suggested.target,
            replicas=suggested.replicas,
            **fields,
        )
        incident.actions.append(action)
        self.save_action(action)
        return action

    def save_action(self, action: Action) -> None:
        if self._db:
            self._db.save_action(action)

    def get(self, incident_id: int) -> Incident | None:
        return self._incidents.get(incident_id)

    def all(self) -> list[Incident]:
        """Newest first."""
        return sorted(self._incidents.values(), key=lambda i: i.id, reverse=True)

    def open_count(self) -> int:
        return len(self._open_by_group)
