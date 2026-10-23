"""On startup, fetch the alerts Alertmanager is currently sending to the agent.

Incidents live in memory, so a restart forgets them, and Alertmanager only repeats a
notification every few hours. Reading the active alerts closes that gap.
"""

from collections import defaultdict

import httpx2

from app.alerts import Notification


async def firing_notifications(
    client: httpx2.AsyncClient, alertmanager_url: str, receiver: str = "agent"
) -> list[Notification]:
    response = await client.get(
        f"{alertmanager_url}/api/v2/alerts",
        params={"active": "true", "silenced": "false", "inhibited": "false"},
    )
    response.raise_for_status()
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for alert in response.json():
        if receiver not in {r.get("name") for r in alert.get("receivers", [])}:
            continue
        labels = alert.get("labels", {})
        # Same grouping as the Alertmanager route: by namespace and alert name.
        groups[(labels.get("alertname", ""), labels.get("namespace", ""))].append(alert)

    notifications = []
    for (alertname, namespace), alerts in groups.items():
        group_labels = {"alertname": alertname, "namespace": namespace}
        notifications.append(
            Notification.model_validate(
                {
                    "version": "4",
                    "groupKey": "catch-up",
                    "status": "firing",
                    "receiver": receiver,
                    "groupLabels": group_labels,
                    "commonLabels": group_labels,
                    "alerts": [
                        {
                            "status": "firing",
                            "labels": a.get("labels", {}),
                            "annotations": a.get("annotations", {}),
                            "startsAt": a["startsAt"],
                            "generatorURL": a.get("generatorURL", ""),
                            "fingerprint": a["fingerprint"],
                        }
                        for a in alerts
                    ],
                }
            )
        )
    return notifications
