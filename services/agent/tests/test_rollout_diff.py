import asyncio
import json
from datetime import UTC, datetime

import httpx2

from app.tools.base import call_tool
from app.tools.kubernetes import KubeApi, kubernetes_tools

NOW = datetime(2026, 10, 22, 23, 30, tzinfo=UTC)


def replicaset(owner, revision, env, created, ready, image="sentinel/payments:abc", checksum="c1"):
    return {
        "metadata": {
            "name": f"{owner}-{revision}",
            "creationTimestamp": created,
            "annotations": {"deployment.kubernetes.io/revision": str(revision)},
            "ownerReferences": [{"kind": "Deployment", "name": owner}],
        },
        "spec": {
            "replicas": 1,
            "template": {
                "metadata": {"annotations": {"checksum/config": checksum}},
                "spec": {"containers": [{"name": owner, "image": image, "env": env}]},
            },
        },
        "status": {"readyReplicas": ready},
    }


DB_PASSWORD = {
    "name": "DB_PASSWORD",
    "valueFrom": {"secretKeyRef": {"name": "orders-db-app", "key": "password"}},
}


def diff(items, deployment="payments", tmp_path=None):
    def handler(request):
        assert request.url.path == "/apis/apps/v1/namespaces/sentinel/replicasets"
        return httpx2.Response(200, json={"items": items})

    token = tmp_path / "token"
    token.write_text("t")
    api = KubeApi(
        "https://kube", token, httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    )
    tools = {t.name: t for t in kubernetes_tools(api, "sentinel", clock=lambda: NOW)}
    return asyncio.run(call_tool(tools, "k8s_rollout_diff", {"deployment": deployment}, 100_000))


def test_shows_the_added_setting_of_a_bad_rollout(tmp_path):
    items = [
        replicaset("payments", 2, [DB_PASSWORD], "2026-10-22T20:00:00Z", ready=1),
        replicaset(
            "payments",
            3,
            [DB_PASSWORD, {"name": "MAX_AMOUNT_CENTS", "value": "abc"}],
            "2026-10-22T23:25:00Z",
            ready=0,
        ),
        replicaset("orders", 7, [], "2026-10-22T23:29:00Z", ready=1),
    ]

    out = json.loads(diff(items, tmp_path=tmp_path))

    assert out["newest"] == {"revision": 3, "created": "5m ago", "replicas": 1, "ready": 0}
    assert out["previous"]["revision"] == 2
    assert out["previous"]["ready"] == 1
    assert out["changes"] == ["payments: env MAX_AMOUNT_CENTS added = abc"]


def test_image_config_and_secret_reference_changes(tmp_path):
    old_env = [{"name": "A", "value": "1"}, {"name": "GONE", "value": "x"}]
    new_env = [{"name": "A", "value": "2"}, DB_PASSWORD]
    items = [
        replicaset("payments", 1, old_env, "2026-10-22T20:00:00Z", 1, image="p:1", checksum="a"),
        replicaset("payments", 2, new_env, "2026-10-22T21:00:00Z", 1, image="p:2", checksum="b"),
    ]

    changes = json.loads(diff(items, tmp_path=tmp_path))["changes"]

    assert changes == [
        "ConfigMap settings changed (checksum/config annotation)",
        "payments: image p:1 -> p:2",
        "payments: env DB_PASSWORD added = <from secret orders-db-app/password>",
        "payments: env GONE removed (was x)",
        "payments: env A changed 1 -> 2",
    ]


def test_single_revision_and_unknown_deployment(tmp_path):
    items = [replicaset("payments", 1, [], "2026-10-22T20:00:00Z", 1)]

    assert json.loads(diff(items, tmp_path=tmp_path))["note"] == "only one revision exists"
    assert diff(items, deployment="nope", tmp_path=tmp_path) == (
        "error: no ReplicaSets found for deployment 'nope'"
    )


def test_identical_templates_say_so(tmp_path):
    items = [
        replicaset("payments", 1, [], "2026-10-22T20:00:00Z", 1),
        replicaset("payments", 2, [], "2026-10-22T21:00:00Z", 1),
    ]

    assert json.loads(diff(items, tmp_path=tmp_path))["changes"] == [
        "no differences in the pod template"
    ]
