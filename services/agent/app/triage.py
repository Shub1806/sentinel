"""Initial findings: the evidence an on-call engineer would look at first, collected with
fixed rules before the model's first turn.

Small models are much better at reading evidence than at deciding where to look, so the
investigation starts from these facts. The same rules, without a model, are also the
"simple rules" baseline for the evaluation.
"""

import json
import time
from dataclasses import dataclass

from app.incidents import Incident
from app.tools.base import Tool, call_tool

# Characters of each finding that go into the model's first message.
FINDING_CHARS = 1500
MAX_PODS = 2
APPS = {"orders", "payments", "notifications"}


@dataclass
class Finding:
    tool: str
    arguments: dict
    result: str
    seconds: float


def _deployment_of(pod_name: str) -> str | None:
    """payments-58f84654df-plx78 -> payments (Deployment pods: <name>-<rs hash>-<pod hash>)."""
    parts = pod_name.rsplit("-", 2)
    return parts[0] if len(parts) == 3 else None


def _unhealthy(pod: dict) -> bool:
    if pod.get("phase") == "Succeeded":  # a finished Job
        return False
    ready, _, total = (pod.get("ready") or "0/0").partition("/")
    return (
        pod.get("phase") != "Running"
        or bool(pod.get("waiting"))
        or ready != total
        or "last_exit" in pod
    )


def _services(incident: Incident) -> list[str]:
    found = []
    for alert in incident.alerts.values():
        labels = alert.labels
        for key in ("job", "service", "redpanda_group"):
            value = labels.get(key)
            if value in APPS and value not in found:
                found.append(value)
        pod_app = _deployment_of(labels.get("pod", "")) if labels.get("pod") else None
        if pod_app in APPS and pod_app not in found:
            found.append(pod_app)
    return found


class Triage:
    def __init__(self, tools: dict[str, Tool], namespace: str, max_chars: int):
        self.tools = tools
        self.namespace = namespace
        self.max_chars = max_chars

    async def _run(self, name: str, args: dict, out: list[Finding]) -> str | None:
        if name not in self.tools:
            return None
        started = time.monotonic()
        result = await call_tool(self.tools, name, args, self.max_chars)
        out.append(Finding(name, args, result, round(time.monotonic() - started, 2)))
        return result

    async def collect(self, incident: Incident) -> list[Finding]:
        out: list[Finding] = []
        pods_text = await self._run("k8s_pods", {}, out)
        bad_pods: list[dict] = []
        if pods_text and not pods_text.startswith("error"):
            try:
                pods = json.loads(pods_text)["pods"]
            except ValueError, KeyError:
                pods = []
            alert_pods = {a.labels.get("pod") for a in incident.alerts.values()}
            bad_pods = [p for p in pods if _unhealthy(p) or p["name"] in alert_pods]
            # Keep only the pods that matter; the model doesn't need the healthy ones.
            out[-1].result = json.dumps(
                {"not_healthy_or_in_alert": bad_pods, "healthy_pods": len(pods) - len(bad_pods)},
                separators=(",", ":"),
            )

        await self._run("k8s_events", {"warnings_only": True}, out)

        deployments: list[str] = []
        for pod in bad_pods[:MAX_PODS]:
            crashed = pod.get("restarts", 0) > 0 or "last_exit" in pod
            await self._run(
                "k8s_pod_logs", {"pod": pod["name"], "previous": crashed, "tail": 30}, out
            )
            deployment = _deployment_of(pod["name"]) if pod.get("owner") == "ReplicaSet" else None
            if deployment and deployment not in deployments:
                deployments.append(deployment)

        for service in _services(incident):
            if service not in deployments:
                deployments.append(service)
            query = (
                f'{{namespace="{self.namespace}", app="{service}"}} '
                '| json | level=~"WARN|WARNING|ERROR"'
            )
            await self._run("loki_logs", {"query": query, "minutes": 15, "limit": 10}, out)

        for deployment in deployments[:MAX_PODS]:
            await self._run("k8s_rollout_diff", {"deployment": deployment}, out)
        return out


def findings_text(findings: list[Finding]) -> str:
    if not findings:
        return ""
    parts = ["Initial findings (collected automatically before you start):"]
    for f in findings:
        args = json.dumps(f.arguments) if f.arguments else ""
        result = f.result
        if len(result) > FINDING_CHARS:
            result = result[:FINDING_CHARS] + "... [cut]"
        parts.append(f"\n## {f.tool} {args}\n{result}")
    return "\n".join(parts)
