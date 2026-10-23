"""Kubernetes tools. Read-only: GET requests only, and only in the watched namespace.

The agent's ServiceAccount enforces the same thing (see infra/charts/agent-access), so a
bug here still can't change or read anything else.
"""

import ssl
from datetime import UTC, datetime
from pathlib import Path
from typing import Self

import httpx2
from pydantic import BaseModel, Field

from app.tools.base import Tool, ToolError

SA_DIR = Path("/var/run/secrets/kubernetes.io/serviceaccount")

# kind -> API path prefix. Secrets are deliberately missing.
KINDS = {
    "deployment": "/apis/apps/v1/namespaces/{ns}/deployments",
    "statefulset": "/apis/apps/v1/namespaces/{ns}/statefulsets",
    "replicaset": "/apis/apps/v1/namespaces/{ns}/replicasets",
    "job": "/apis/batch/v1/namespaces/{ns}/jobs",
    "hpa": "/apis/autoscaling/v2/namespaces/{ns}/horizontalpodautoscalers",
    "service": "/api/v1/namespaces/{ns}/services",
    "configmap": "/api/v1/namespaces/{ns}/configmaps",
    "pvc": "/api/v1/namespaces/{ns}/persistentvolumeclaims",
    "postgres-cluster": "/apis/postgresql.cnpg.io/v1/namespaces/{ns}/clusters",
}


class KubeApi:
    """A small client for the Kubernetes API, using the pod's ServiceAccount token."""

    def __init__(self, base_url: str, token_file: Path, client: httpx2.AsyncClient):
        self.base_url = base_url
        self.token_file = token_file
        self.client = client

    @classmethod
    def in_cluster(cls, timeout: float, env) -> Self | None:
        host, port = env.get("KUBERNETES_SERVICE_HOST"), env.get("KUBERNETES_SERVICE_PORT")
        if not host or not (SA_DIR / "token").exists():
            return None
        tls = ssl.create_default_context(cafile=str(SA_DIR / "ca.crt"))
        client = httpx2.AsyncClient(verify=tls, timeout=timeout)
        return cls(f"https://{host}:{port}", SA_DIR / "token", client)

    async def get(self, path: str, params: dict | None = None, text: bool = False):
        # Read the token every time: Kubernetes replaces it before it expires.
        token = self.token_file.read_text().strip()
        response = await self.client.get(
            self.base_url + path, params=params, headers={"Authorization": f"Bearer {token}"}
        )
        if response.status_code == 404:
            raise ToolError("not found")
        if response.status_code == 403:
            raise ToolError("forbidden: the agent has no permission for this")
        if response.status_code != 200:
            raise ToolError(f"Kubernetes API said {response.status_code}: {response.text[:300]}")
        return response.text if text else response.json()


def _age(timestamp: str | None, now: datetime) -> str | None:
    if not timestamp:
        return None
    seconds = int((now - datetime.fromisoformat(timestamp.replace("Z", "+00:00"))).total_seconds())
    if seconds < 120:
        return f"{seconds}s"
    if seconds < 7200:
        return f"{seconds // 60}m"
    return f"{seconds // 3600}h"


def pod_summary(pod: dict, now: datetime) -> dict:
    status = pod.get("status", {})
    containers = status.get("containerStatuses", [])
    summary = {
        "name": pod["metadata"]["name"],
        "phase": status.get("phase"),
        "ready": f"{sum(1 for c in containers if c.get('ready'))}/{len(containers)}",
        "restarts": sum(c.get("restartCount", 0) for c in containers),
        "age": _age(pod["metadata"].get("creationTimestamp"), now),
        "owner": next((o["kind"] for o in pod["metadata"].get("ownerReferences", [])), None),
    }
    for c in containers:
        waiting = c.get("state", {}).get("waiting")
        if waiting:
            summary["waiting"] = waiting.get("reason")
        last = c.get("lastState", {}).get("terminated")
        if last:
            summary["last_exit"] = {
                "reason": last.get("reason"),
                "exit_code": last.get("exitCode"),
                "ago": _age(last.get("finishedAt"), now),
            }
    return summary


def _trim(obj: dict) -> dict:
    """Drops bulky fields the model doesn't need."""
    meta = obj.get("metadata", {})
    meta.pop("managedFields", None)
    annotations = meta.get("annotations", {})
    annotations.pop("kubectl.kubernetes.io/last-applied-configuration", None)
    annotations.pop("meta.helm.sh/release-name", None)
    annotations.pop("meta.helm.sh/release-namespace", None)
    return obj


class NoParams(BaseModel):
    pass


class EventParams(BaseModel):
    object_name: str | None = Field(
        default=None, description="Only events about this object, e.g. a pod or deployment name"
    )
    warnings_only: bool = Field(default=False, description="Only Warning events")


class LogParams(BaseModel):
    pod: str = Field(description="Pod name")
    previous: bool = Field(
        default=False,
        description="Logs of the previous (crashed) container instead of the current one",
    )
    tail: int = Field(default=50, ge=1, le=300, description="Number of lines from the end")


class GetParams(BaseModel):
    kind: str = Field(description="One of: " + ", ".join(KINDS))
    name: str | None = Field(default=None, description="Object name; leave out to list all")


def kubernetes_tools(api: KubeApi | None, namespace: str, clock=lambda: datetime.now(UTC)):
    def need_api() -> KubeApi:
        if api is None:
            raise ToolError("not running inside Kubernetes, so the Kubernetes tools are off")
        return api

    async def pods(_: NoParams) -> dict:
        body = await need_api().get(f"/api/v1/namespaces/{namespace}/pods")
        now = clock()
        return {"pods": [pod_summary(p, now) for p in body["items"]]}

    async def events(p: EventParams) -> dict:
        params = {}
        if p.object_name:
            params["fieldSelector"] = f"involvedObject.name={p.object_name}"
        body = await need_api().get(f"/api/v1/namespaces/{namespace}/events", params)
        now = clock()
        items = []
        for e in body["items"]:
            if p.warnings_only and e.get("type") != "Warning":
                continue
            last = (
                e.get("lastTimestamp")
                or e.get("eventTime")
                or e["metadata"].get("creationTimestamp")
            )
            involved = e["involvedObject"]
            items.append(
                (
                    last or "",
                    {
                        "type": e.get("type"),
                        "reason": e.get("reason"),
                        "object": f"{involved.get('kind')}/{involved.get('name')}",
                        "message": (e.get("message") or "")[:300],
                        "count": e.get("count") or 1,
                        "last_seen": _age(last, now),
                    },
                )
            )
        items.sort(key=lambda pair: pair[0], reverse=True)
        return {"events": [event for _, event in items[:40]], "total": len(items)}

    async def pod_logs(p: LogParams) -> dict:
        text = await need_api().get(
            f"/api/v1/namespaces/{namespace}/pods/{p.pod}/log",
            {"tailLines": p.tail, "previous": str(p.previous).lower()},
            text=True,
        )
        return {
            "pod": p.pod,
            "previous": p.previous,
            "lines": [line[:400] for line in text.splitlines()],
        }

    async def get(p: GetParams) -> dict:
        path = KINDS.get(p.kind)
        if path is None:
            raise ToolError(f"kind must be one of: {', '.join(KINDS)}")
        path = path.format(ns=namespace)
        if p.name:
            return _trim(await need_api().get(f"{path}/{p.name}"))
        body = await need_api().get(path)
        return {"items": [_trim(i) for i in body["items"]]}

    return [
        Tool(
            "k8s_pods",
            f"List pods in namespace {namespace}: phase, ready, restarts, waiting reason "
            "(e.g. CrashLoopBackOff) and the last exit reason/code.",
            NoParams,
            pods,
        ),
        Tool(
            "k8s_events",
            f"Recent Kubernetes events in namespace {namespace}, newest first "
            "(scheduling, probe failures, kills, scaling).",
            EventParams,
            events,
        ),
        Tool(
            "k8s_pod_logs",
            "The last lines of one pod's log straight from Kubernetes. With previous=true, the "
            "log of the container that crashed before the current one.",
            LogParams,
            pod_logs,
        ),
        Tool(
            "k8s_get",
            f"Get one object, or list all of a kind, in namespace {namespace}. Kinds: "
            + ", ".join(KINDS)
            + ". Secrets are not available.",
            GetParams,
            get,
        ),
    ]
