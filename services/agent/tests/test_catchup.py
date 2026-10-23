import asyncio

import httpx2

from app.catchup import firing_notifications


def am_alert(name, receiver, pod, fingerprint):
    return {
        "labels": {"alertname": name, "namespace": "sentinel", "pod": pod},
        "annotations": {"summary": f"{pod} restarted"},
        "startsAt": "2026-10-22T23:25:00Z",
        "endsAt": "2026-10-22T23:45:00Z",
        "fingerprint": fingerprint,
        "receivers": [{"name": receiver}],
        "status": {"state": "active"},
    }


def test_groups_active_agent_alerts_like_the_route():
    seen = []

    def handler(request):
        seen.append(request.url.params)
        return httpx2.Response(
            200,
            json=[
                am_alert("PodRestarted", "agent", "payments-1", "a"),
                am_alert("PodRestarted", "agent", "payments-2", "b"),
                am_alert("Watchdog", "null", "", "c"),
            ],
        )

    client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    notifications = asyncio.run(firing_notifications(client, "http://am:9093"))

    assert len(notifications) == 1
    n = notifications[0]
    assert n.incident_key == "alertname=PodRestarted,namespace=sentinel"
    assert [a.fingerprint for a in n.alerts] == ["a", "b"]
    assert all(a.status == "firing" and a.ends_at is None for a in n.alerts)
    assert seen[0]["active"] == "true" and seen[0]["inhibited"] == "false"
