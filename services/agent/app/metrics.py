"""Prometheus metrics for the agent. Served at GET /metrics."""

from prometheus_client import Counter, Gauge, Histogram, disable_created_metrics

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

INVESTIGATIONS = Counter(
    "agent_investigations",  # exported as agent_investigations_total
    "Finished investigations, by status (done or failed).",
    ["status"],
)
for _status in ("done", "failed"):
    INVESTIGATIONS.labels(status=_status)

INVESTIGATION_SECONDS = Histogram(
    "agent_investigation_seconds",
    "How long an investigation took, from start to report.",
    buckets=(5, 10, 20, 30, 60, 120, 180, 300, 600),
)

TOOL_CALLS = Counter(
    "agent_tool_calls",  # exported as agent_tool_calls_total
    "Tool calls made during investigations, by tool.",
    ["tool"],
)
