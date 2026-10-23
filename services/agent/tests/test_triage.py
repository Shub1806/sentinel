import asyncio
import json

from pydantic import BaseModel, ConfigDict

from app.alerts import Notification
from app.incidents import IncidentStore
from app.tools.base import Tool
from app.triage import FINDING_CHARS, Triage, findings_text

PODS = [
    {
        "name": "payments-58f84654df-plx78",
        "phase": "Running",
        "ready": "0/1",
        "restarts": 6,
        "owner": "ReplicaSet",
        "waiting": "CrashLoopBackOff",
        "last_exit": {"reason": "Error", "exit_code": 3, "ago": "20s"},
    },
    {
        "name": "payments-6fcc5d7cb4-vchkj",
        "phase": "Running",
        "ready": "1/1",
        "restarts": 0,
        "owner": "ReplicaSet",
    },
    {
        "name": "orders-78f8686c55-xc9gx",
        "phase": "Running",
        "ready": "1/1",
        "restarts": 0,
        "owner": "ReplicaSet",
    },
    {
        "name": "redpanda-create-topics-hqprv",
        "phase": "Succeeded",
        "ready": "0/1",
        "restarts": 2,
        "owner": "Job",
    },
]


class AnyArgs(BaseModel):
    model_config = ConfigDict(extra="allow")


def fake_tools(calls: list):
    def tool(name, result):
        async def run(params):
            calls.append((name, params.model_dump()))
            return result

        return Tool(name, name, AnyArgs, run)

    return {
        t.name: t
        for t in [
            tool("k8s_pods", {"pods": PODS}),
            tool("k8s_events", {"events": [{"reason": "BackOff"}]}),
            tool("k8s_pod_logs", {"lines": ["ValueError: invalid literal for int(): 'abc'"]}),
            tool("loki_logs", {"lines": []}),
            tool("k8s_rollout_diff", {"changes": ["payments: env MAX_AMOUNT_CENTS added = abc"]}),
        ]
    }


def pod_restarted_incident():
    body = {
        "version": "4",
        "groupKey": "k",
        "status": "firing",
        "receiver": "agent",
        "groupLabels": {"alertname": "PodRestarted", "namespace": "sentinel"},
        "alerts": [
            {
                "status": "firing",
                "labels": {
                    "alertname": "PodRestarted",
                    "namespace": "sentinel",
                    "pod": "payments-58f84654df-plx78",
                },
                "startsAt": "2026-10-22T23:25:00Z",
                "fingerprint": "f1",
            }
        ],
    }
    incident, _ = IncidentStore().record(Notification.model_validate(body))
    return incident


def test_collects_what_an_engineer_would_look_at_first():
    calls = []
    findings = asyncio.run(
        Triage(fake_tools(calls), "sentinel", 6000).collect(pod_restarted_incident())
    )

    assert calls == [
        ("k8s_pods", {}),
        ("k8s_events", {"warnings_only": True}),
        ("k8s_pod_logs", {"pod": "payments-58f84654df-plx78", "previous": True, "tail": 30}),
        (
            "loki_logs",
            {
                "query": '{namespace="sentinel", app="payments"} '
                '| json | level=~"WARN|WARNING|ERROR"',
                "minutes": 15,
                "limit": 10,
            },
        ),
        ("k8s_rollout_diff", {"deployment": "payments"}),
    ]
    pods = json.loads(findings[0].result)
    # Healthy pods and finished Jobs are left out; only the crashing pod remains.
    assert [p["name"] for p in pods["not_healthy_or_in_alert"]] == ["payments-58f84654df-plx78"]
    assert pods["healthy_pods"] == 3


def test_findings_text_is_cut_per_finding():
    calls = []
    findings = asyncio.run(
        Triage(fake_tools(calls), "sentinel", 6000).collect(pod_restarted_incident())
    )
    findings[2].result = "x" * (FINDING_CHARS + 500)

    text = findings_text(findings)

    assert text.startswith("Initial findings (collected automatically")
    assert "## k8s_rollout_diff" in text
    assert "MAX_AMOUNT_CENTS added = abc" in text
    assert "x" * FINDING_CHARS + "... [cut]" in text


def test_missing_tools_are_skipped():
    findings = asyncio.run(Triage({}, "sentinel", 6000).collect(pod_restarted_incident()))
    assert findings == []
    assert findings_text(findings) == ""
