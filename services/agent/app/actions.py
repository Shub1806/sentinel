"""Carries out a suggested action, only after a person approved it on the page.

The agent's own ServiceAccount can only read. To change anything, it asks Kubernetes for a
short-lived token (10 minutes) for a second ServiceAccount, "agent-actions", which may only
patch the allow-listed Deployments (see infra/charts/agent-access). Nothing else is possible:
no delete, no other kinds, no other namespace.
"""

import asyncio
import copy
import logging
from datetime import UTC, datetime

from app.tools.base import ToolError
from app.tools.kubernetes import KubeApi, _revision

log = logging.getLogger("agent.actions")

EXECUTABLE = ("restart_deployment", "scale_deployment", "rollout_undo")


class ActionError(Exception):
    """The action could not be carried out. The message is shown on the page."""


def why_not(kind: str, target: str, replicas: int | None, allowed: tuple[str, ...]) -> str | None:
    """Why this suggestion can't be approved, or None if it can."""
    if kind not in EXECUTABLE:
        return None if kind in ("none", "manual") else f"unknown action {kind!r}"
    if target not in allowed:
        return f"{target or '(no target)'} is not on the list of deployments the agent may change"
    if kind == "scale_deployment" and replicas is None:
        return "no number of pods was given"
    return None


class Executor:
    def __init__(
        self,
        api: KubeApi | None,
        namespace: str,
        own_namespace: str,
        actions_account: str,
        allowed: tuple[str, ...],
        clock=lambda: datetime.now(UTC),
        sleep=asyncio.sleep,
        wait_seconds: float = 120,
        poll_seconds: float = 5,
    ):
        self.api = api
        self.namespace = namespace
        self.own_namespace = own_namespace
        self.actions_account = actions_account
        self.allowed = allowed
        self.clock = clock
        self.sleep = sleep
        self.wait_seconds = wait_seconds
        self.poll_seconds = poll_seconds

    def _deployment_path(self, name: str) -> str:
        return f"/apis/apps/v1/namespaces/{self.namespace}/deployments/{name}"

    async def _token(self) -> str:
        """A 10-minute token for the actions account (TokenRequest API)."""
        body = {
            "apiVersion": "authentication.k8s.io/v1",
            "kind": "TokenRequest",
            "spec": {"expirationSeconds": 600},
        }
        path = (
            f"/api/v1/namespaces/{self.own_namespace}/serviceaccounts/{self.actions_account}/token"
        )
        return (await self.api.send("POST", path, body))["status"]["token"]

    async def run(self, kind: str, target: str, replicas: int | None = None) -> str:
        """Carries out the action and waits for the rollout. Returns a short result."""
        reason = why_not(kind, target, replicas, self.allowed)
        if reason or kind not in EXECUTABLE:
            raise ActionError(reason or f"{kind} is not something the agent carries out")
        if self.api is None:
            raise ActionError("not running inside Kubernetes")
        try:
            token = await self._token()
            if kind == "restart_deployment":
                did = await self._restart(target, token)
            elif kind == "scale_deployment":
                did = await self._scale(target, replicas, token)
            else:
                did = await self._undo(target, token)
            return f"{did}; {await self._wait(target)}"
        except ToolError as exc:
            raise ActionError(str(exc)) from exc

    async def _restart(self, name: str, token: str) -> str:
        # The same change "kubectl rollout restart" makes: a new annotation, so new pods start.
        stamp = self.clock().isoformat()
        patch = {
            "spec": {
                "template": {
                    "metadata": {"annotations": {"kubectl.kubernetes.io/restartedAt": stamp}}
                }
            }
        }
        await self.api.send(
            "PATCH",
            self._deployment_path(name),
            patch,
            "application/strategic-merge-patch+json",
            token,
        )
        return f"restarted {name}"

    async def _scale(self, name: str, replicas: int, token: str) -> str:
        hpas = await self.api.get(
            f"/apis/autoscaling/v2/namespaces/{self.namespace}/horizontalpodautoscalers"
        )
        for hpa in hpas["items"]:
            if hpa["spec"]["scaleTargetRef"].get("name") == name:
                raise ActionError(
                    f"{name} is autoscaled by {hpa['metadata']['name']}; the autoscaler would "
                    "undo a manual scale. Change its min/max replicas instead."
                )
        await self.api.send(
            "PATCH",
            self._deployment_path(name) + "/scale",
            {"spec": {"replicas": replicas}},
            "application/merge-patch+json",
            token,
        )
        return f"scaled {name} to {replicas}"

    async def _undo(self, name: str, token: str) -> str:
        # What "kubectl rollout undo" does: put back the pod template of the previous revision.
        deployment = await self.api.get(self._deployment_path(name))
        current = int(
            (deployment["metadata"].get("annotations") or {}).get(
                "deployment.kubernetes.io/revision", "0"
            )
        )
        replica_sets = await self.api.get(f"/apis/apps/v1/namespaces/{self.namespace}/replicasets")
        older = [
            rs
            for rs in replica_sets["items"]
            if any(
                o.get("kind") == "Deployment" and o.get("name") == name
                for o in rs["metadata"].get("ownerReferences", [])
            )
            and _revision(rs) < current
        ]
        if not older:
            raise ActionError(f"{name} has no earlier revision to go back to")
        previous = max(older, key=_revision)
        template = copy.deepcopy(previous["spec"]["template"])
        template["metadata"].get("labels", {}).pop("pod-template-hash", None)
        await self.api.send(
            "PATCH",
            self._deployment_path(name),
            [{"op": "replace", "path": "/spec/template", "value": template}],
            "application/json-patch+json",
            token,
        )
        return f"rolled {name} back from revision {current} to {_revision(previous)}"

    async def _wait(self, name: str) -> str:
        """Waits until the new pods are up, like "kubectl rollout status"."""
        waited = 0.0
        while True:
            d = await self.api.get(self._deployment_path(name))
            spec, status = d.get("spec", {}), d.get("status", {})
            want = spec.get("replicas", 1)
            updated = status.get("updatedReplicas", 0)
            available = status.get("availableReplicas", 0)
            done = (
                status.get("observedGeneration", 0) >= d["metadata"].get("generation", 0)
                and updated == want
                and available == want
                and status.get("replicas", 0) == want
            )
            if done:
                return f"rolled out ({available}/{want} pods available after {waited:.0f} s)"
            if waited >= self.wait_seconds:
                return (
                    f"still rolling after {waited:.0f} s "
                    f"({updated}/{want} updated, {available} available)"
                )
            await self.sleep(self.poll_seconds)
            waited += self.poll_seconds
