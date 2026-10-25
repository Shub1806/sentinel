import asyncio
import json
from datetime import UTC, datetime

import httpx2
import pytest

from app.actions import ActionError, Executor, why_not
from app.tools.kubernetes import KubeApi

NOW = datetime(2026, 10, 24, 23, 30, tzinfo=UTC)
ALLOWED = ("orders", "payments", "notifications")
DEPLOY = "/apis/apps/v1/namespaces/sentinel/deployments/payments"


def deployment(revision=9, generation=5, observed=5, updated=1, available=1, replicas=1):
    return {
        "metadata": {
            "name": "payments",
            "generation": generation,
            "annotations": {"deployment.kubernetes.io/revision": str(revision)},
        },
        "spec": {"replicas": 1},
        "status": {
            "observedGeneration": observed,
            "updatedReplicas": updated,
            "availableReplicas": available,
            "replicas": replicas,
        },
    }


def replicaset(owner, revision, env):
    return {
        "metadata": {
            "annotations": {"deployment.kubernetes.io/revision": str(revision)},
            "ownerReferences": [{"kind": "Deployment", "name": owner}],
        },
        "spec": {
            "template": {
                "metadata": {"labels": {"app": owner, "pod-template-hash": f"h{revision}"}},
                "spec": {"containers": [{"name": owner, "env": env}]},
            }
        },
    }


class FakeKube:
    """A fake API server: answers GETs from a dict, records every write."""

    def __init__(self, gets: dict, deployments=None, write_status=200):
        self.gets = gets
        self.deployments = list(deployments or [deployment()])  # successive GETs of the deployment
        self.writes = []
        self.write_status = write_status

    def __call__(self, request):
        path = request.url.path
        if request.method == "GET":
            if path == DEPLOY:
                body = self.deployments.pop(0) if len(self.deployments) > 1 else self.deployments[0]
                return httpx2.Response(200, json=body)
            return httpx2.Response(200, json=self.gets[path])
        self.writes.append(
            (
                request.method,
                path,
                request.headers["content-type"],
                request.headers["authorization"],
                json.loads(request.content),
            )
        )
        if path.endswith("/token"):
            return httpx2.Response(201, json={"status": {"token": "short-lived"}})
        if self.write_status != 200:
            return httpx2.Response(self.write_status, json={"message": "patch denied"})
        return httpx2.Response(200, json={})


def executor(fake, tmp_path, wait_seconds=120):
    token = tmp_path / "token"
    token.write_text("read-only-token")
    api = KubeApi("https://kube", token, httpx2.AsyncClient(transport=httpx2.MockTransport(fake)))
    slept = []

    async def sleep(s):
        slept.append(s)

    ex = Executor(
        api, "sentinel", "agent", "agent-actions", ALLOWED, lambda: NOW, sleep, wait_seconds
    )
    return ex, slept


def run(ex, *args):
    return asyncio.run(ex.run(*args))


def test_restart_uses_a_short_lived_token_for_the_actions_account(tmp_path):
    fake = FakeKube({})
    ex, _ = executor(fake, tmp_path)

    result = run(ex, "restart_deployment", "payments")

    token_req, patch = fake.writes
    assert token_req[1] == "/api/v1/namespaces/agent/serviceaccounts/agent-actions/token"
    assert token_req[3] == "Bearer read-only-token"  # asked for with the agent's own account
    assert token_req[4]["spec"]["expirationSeconds"] == 600
    assert patch[:4] == (
        "PATCH",
        DEPLOY,
        "application/strategic-merge-patch+json",
        "Bearer short-lived",
    )
    annotations = patch[4]["spec"]["template"]["metadata"]["annotations"]
    assert annotations == {"kubectl.kubernetes.io/restartedAt": NOW.isoformat()}
    assert result == "restarted payments; rolled out (1/1 pods available after 0 s)"


def test_undo_puts_back_the_previous_revisions_pod_template(tmp_path):
    rs = "/apis/apps/v1/namespaces/sentinel/replicasets"
    items = [
        replicaset("payments", 8, []),
        replicaset("payments", 9, [{"name": "MAX_AMOUNT_CENTS", "value": "abc"}]),
        replicaset("payments", 7, [{"name": "OLD", "value": "x"}]),
        replicaset("orders", 12, []),
    ]
    fake = FakeKube({rs: {"items": items}})
    ex, _ = executor(fake, tmp_path)

    result = run(ex, "rollout_undo", "payments")

    method, path, content_type, auth, body = fake.writes[1]
    assert (method, path, content_type, auth) == (
        "PATCH",
        DEPLOY,
        "application/json-patch+json",
        "Bearer short-lived",
    )
    assert body[0]["op"] == "replace" and body[0]["path"] == "/spec/template"
    template = body[0]["value"]
    assert template["spec"]["containers"][0]["env"] == []  # revision 8: no MAX_AMOUNT_CENTS
    assert "pod-template-hash" not in template["metadata"]["labels"]
    assert result.startswith("rolled payments back from revision 9 to 8; rolled out")


def test_undo_without_an_earlier_revision_fails_cleanly(tmp_path):
    rs = "/apis/apps/v1/namespaces/sentinel/replicasets"
    fake = FakeKube({rs: {"items": [replicaset("payments", 9, [])]}})
    ex, _ = executor(fake, tmp_path)

    with pytest.raises(ActionError, match="no earlier revision"):
        run(ex, "rollout_undo", "payments")
    assert [w[1].endswith("/token") for w in fake.writes] == [True]  # nothing was patched


def test_scale_is_refused_for_an_autoscaled_deployment(tmp_path):
    hpas = "/apis/autoscaling/v2/namespaces/sentinel/horizontalpodautoscalers"
    fake = FakeKube(
        {
            hpas: {
                "items": [
                    {
                        "metadata": {"name": "payments"},
                        "spec": {"scaleTargetRef": {"name": "payments"}},
                    }
                ]
            }
        }
    )
    ex, _ = executor(fake, tmp_path)

    with pytest.raises(ActionError, match="autoscaled by payments"):
        run(ex, "scale_deployment", "payments", 3)


def test_scale_patches_the_scale_subresource(tmp_path):
    hpas = "/apis/autoscaling/v2/namespaces/sentinel/horizontalpodautoscalers"
    fake = FakeKube({hpas: {"items": []}})
    ex, _ = executor(fake, tmp_path)

    run(ex, "scale_deployment", "payments", 2)

    method, path, content_type, _, body = fake.writes[1]
    assert (method, path, content_type) == (
        "PATCH",
        DEPLOY + "/scale",
        "application/merge-patch+json",
    )
    assert body == {"spec": {"replicas": 2}}


def test_waits_for_the_rollout_and_reports_when_it_is_slow(tmp_path):
    rolling = deployment(generation=6, observed=6, updated=1, available=0, replicas=2)
    fake = FakeKube({}, deployments=[rolling, rolling, deployment(generation=6, observed=6)])
    ex, slept = executor(fake, tmp_path)
    assert run(ex, "restart_deployment", "payments").endswith(
        "rolled out (1/1 pods available after 10 s)"
    )
    assert slept == [5, 5]

    fake = FakeKube({}, deployments=[rolling])
    ex, _ = executor(fake, tmp_path, wait_seconds=10)
    assert run(ex, "restart_deployment", "payments").endswith(
        "still rolling after 10 s (1/1 updated, 0 available)"
    )


def test_forbidden_write_is_reported(tmp_path):
    fake = FakeKube({}, write_status=403)
    ex, _ = executor(fake, tmp_path)

    with pytest.raises(ActionError, match="forbidden: patch denied"):
        run(ex, "restart_deployment", "payments")


def test_refuses_without_calling_the_api(tmp_path):
    fake = FakeKube({})
    ex, _ = executor(fake, tmp_path)

    with pytest.raises(ActionError, match="not on the list"):
        run(ex, "restart_deployment", "redpanda")
    with pytest.raises(ActionError, match="not something the agent carries out"):
        run(ex, "manual", "payments")
    assert fake.writes == []


def test_why_not():
    assert why_not("rollout_undo", "payments", None, ALLOWED) is None
    assert why_not("none", "", None, ALLOWED) is None
    assert why_not("restart_deployment", "orders-db", None, ALLOWED).startswith("orders-db is not")
    assert why_not("scale_deployment", "orders", None, ALLOWED) == "no number of pods was given"
    assert why_not("delete_namespace", "x", None, ALLOWED) == "unknown action 'delete_namespace'"


def test_not_in_a_cluster():
    ex = Executor(None, "sentinel", "agent", "agent-actions", ALLOWED)
    with pytest.raises(ActionError, match="not running inside Kubernetes"):
        asyncio.run(ex.run("restart_deployment", "payments"))
