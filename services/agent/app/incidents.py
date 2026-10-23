"""Turns Alertmanager notifications into incidents.

Alertmanager groups alerts (by alert name and namespace, see the route in
infra/helm-values/monitoring.yaml) and sends the whole group again whenever it changes.
One group that starts firing opens one incident. The incident is resolved once every
alert in it is resolved. If the same group fires again later, that is a new incident.

Kept in memory for now: a restart forgets the incidents (they move to SQLite later).
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from itertools import count

from app.alerts import Alert, Notification


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
    def __init__(self, clock=lambda: datetime.now(UTC)) -> None:
        self._clock = clock
        self._ids = count(1)
        self._incidents: dict[int, Incident] = {}
        self._open_by_group: dict[str, int] = {}

    def record(self, notification: Notification) -> tuple[Incident, bool]:
        """Applies one notification. Returns the incident and whether it was just opened."""
        now = self._clock()
        incident_id = self._open_by_group.get(notification.group_key)
        opened = incident_id is None
        if opened:
            incident = Incident(
                id=next(self._ids),
                group_key=notification.group_key,
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
        return incident, opened

    def get(self, incident_id: int) -> Incident | None:
        return self._incidents.get(incident_id)

    def all(self) -> list[Incident]:
        """Newest first."""
        return sorted(self._incidents.values(), key=lambda i: i.id, reverse=True)

    def open_count(self) -> int:
        return len(self._open_by_group)
