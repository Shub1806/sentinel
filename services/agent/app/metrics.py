"""Prometheus metrics for the agent. Served at GET /metrics."""

from prometheus_client import Counter, Gauge, disable_created_metrics

# Skip the extra "<name>_created" timestamp series: they add noise and we don't use them.
disable_created_metrics()

NOTIFICATIONS_RECEIVED = Counter(
    "agent_notifications_received",  # exported as agent_notifications_received_total
    "Alertmanager notifications received, by group status.",
    ["status"],
)
for _status in ("firing", "resolved"):
    NOTIFICATIONS_RECEIVED.labels(status=_status)

INCIDENTS_OPENED = Counter(
    "agent_incidents_opened",  # exported as agent_incidents_opened_total
    "Incidents opened.",
)

OPEN_INCIDENTS = Gauge("agent_open_incidents", "Incidents that are still firing.")
