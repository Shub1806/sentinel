"""Alertmanager webhook bodies, shaped like the real ones (format version 4)."""

FIRING_SINCE = "2026-10-22T23:15:05.123Z"
STILL_FIRING = "0001-01-01T00:00:00Z"  # what Alertmanager sends as endsAt while firing


def alert(job: str, status: str = "firing", ends_at: str = STILL_FIRING) -> dict:
    return {
        "status": status,
        "labels": {
            "alertname": "InvalidMessagesReceived",
            "job": job,
            "namespace": "sentinel",
            "severity": "warning",
        },
        "annotations": {
            "summary": f"{job} skipped invalid messages",
            "description": f"{job} skipped messages that failed validation in the last 10 minutes.",
        },
        "startsAt": FIRING_SINCE,
        "endsAt": ends_at,
        "generatorURL": "http://prometheus:9090/graph?g0.expr=...",
        "fingerprint": f"fp-{job}",
    }


def notification(*alerts: dict, group: str = "InvalidMessagesReceived") -> dict:
    statuses = {a["status"] for a in alerts}
    return {
        "version": "4",
        "groupKey": f'{{}}/{{namespace="sentinel"}}:{{alertname="{group}", namespace="sentinel"}}',
        "truncatedAlerts": 0,
        "status": "firing" if "firing" in statuses else "resolved",
        "receiver": "agent",
        "groupLabels": {"alertname": group, "namespace": "sentinel"},
        "commonLabels": {"alertname": group, "namespace": "sentinel", "severity": "warning"},
        "commonAnnotations": {},
        "externalURL": "http://alertmanager:9093",
        "alerts": list(alerts),
    }
