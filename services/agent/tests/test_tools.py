import asyncio
import json
from datetime import UTC, datetime

import httpx2
import pytest

from app.tools.base import Tool, call_tool, to_text
from app.tools.kubernetes import KubeApi, kubernetes_tools, pod_summary
from app.tools.loki import loki_tools
from app.tools.prometheus import prometheus_tools

NOW = datetime(2026, 10, 22, 23, 30, tzinfo=UTC)
PROM = "http://prom:9090"
LOKI = "http://loki:3100"


class Recorder:
    """A fake HTTP server: records each request and answers with the given handler."""

    def __init__(self, handler):
        self.requests: list[httpx2.Request] = []
        self.handler = handler

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        return self.handler(request)

    def client(self) -> httpx2.AsyncClient:
        return httpx2.AsyncClient(transport=httpx2.MockTransport(self))


def run(tools: list[Tool], name: str, args: dict, max_chars: int = 100_000) -> str:
    return asyncio.run(call_tool({t.name: t for t in tools}, name, args, max_chars))


def run_json(tools, name, args) -> dict:
    text = run(tools, name, args)
    assert not text.startswith("error"), text
    return json.loads(text)


# ---------- Prometheus ----------


def prom_ok(result):
    return lambda request: httpx2.Response(
        200, json={"status": "success", "data": {"resultType": "vector", "result": result}}
    )


def test_prometheus_query_returns_labels_and_values():
    server = Recorder(prom_ok([{"metric": {"job": "orders"}, "value": [1, "0.25"]}]))
    tools = prometheus_tools(server.client(), PROM)

    out = run_json(tools, "prometheus_query", {"query": 'up{namespace="sentinel"}'})

    assert out == {"series": [{"labels": {"job": "orders"}, "value": 0.25}], "total_series": 1}
    request = server.requests[0]
    assert request.url.path == "/api/v1/query"
    assert request.url.params["query"] == 'up{namespace="sentinel"}'


def test_prometheus_query_caps_the_number_of_series():
    result = [{"metric": {"pod": f"p{i}"}, "value": [1, "1"]} for i in range(100)]
    tools = prometheus_tools(Recorder(prom_ok(result)).client(), PROM)

    out = run_json(tools, "prometheus_query", {"query": "up"})

    assert len(out["series"]) == 30
    assert out["total_series"] == 100


def test_prometheus_error_is_reported_to_the_model():
    server = Recorder(
        lambda r: httpx2.Response(
            400, json={"status": "error", "error": "parse error: unexpected }"}
        )
    )
    tools = prometheus_tools(server.client(), PROM)

    out = run(tools, "prometheus_query", {"query": "up{"})

    assert out == "error: Prometheus said 400: parse error: unexpected }"


def test_prometheus_range_summarises_each_series():
    values = [[NOW.timestamp() - 60 * (5 - i), str(v)] for i, v in enumerate([0, 30, 60, 90, 120])]
    server = Recorder(
        lambda r: httpx2.Response(
            200,
            json={
                "status": "success",
                "data": {"result": [{"metric": {"job": "orders"}, "values": values}]},
            },
        )
    )
    tools = prometheus_tools(server.client(), PROM, clock=lambda: NOW)

    out = run_json(tools, "prometheus_range", {"query": "outbox_oldest_pending_age_seconds"})

    series = out["series"][0]
    assert (series["min"], series["max"]) == (0.0, 120.0)
    assert series["first"] == ["23:25", 0.0]
    assert series["last"] == ["23:29", 120.0]
    params = server.requests[0].url.params
    assert float(params["end"]) - float(params["start"]) == 30 * 60
    assert params["step"] == "60"


# ---------- Loki ----------


def test_loki_must_stay_in_the_watched_namespace():
    server = Recorder(lambda r: httpx2.Response(500))
    tools = loki_tools(server.client(), LOKI, "sentinel")

    out = run(tools, "loki_logs", {"query": '{namespace="kube-system"}'})

    assert out == 'error: the query must select namespace="sentinel"'
    assert server.requests == []


def test_loki_merges_streams_newest_first_and_cuts_long_lines():
    def ts(second: int) -> str:
        return str(int((NOW.timestamp() - 60 + second) * 1e9))

    body = {
        "data": {
            "result": [
                {"stream": {"pod": "payments-1"}, "values": [[ts(30), "late"], [ts(10), "early"]]},
                {"stream": {"pod": "orders-1"}, "values": [[ts(20), "x" * 1000]]},
            ]
        }
    }
    server = Recorder(lambda r: httpx2.Response(200, json=body))
    tools = loki_tools(server.client(), LOKI, "sentinel", clock=lambda: NOW)

    out = run_json(tools, "loki_logs", {"query": '{namespace="sentinel"} |= "x"', "minutes": 5})

    assert [line["pod"] for line in out["lines"]] == ["payments-1", "orders-1", "payments-1"]
    assert len(out["lines"][1]["line"]) == 400
    params = server.requests[0].url.params
    assert params["direction"] == "backward"
    assert int(params["end"]) - int(params["start"]) == 5 * 60 * 10**9


# ---------- Kubernetes ----------

CRASHING_POD = {
    "metadata": {
        "name": "payments-abc",
        "creationTimestamp": "2026-10-22T23:00:00Z",
        "ownerReferences": [{"kind": "ReplicaSet", "name": "payments-5d"}],
    },
    "status": {
        "phase": "Running",
        "containerStatuses": [
            {
                "ready": False,
                "restartCount": 4,
                "state": {"waiting": {"reason": "CrashLoopBackOff"}},
                "lastState": {
                    "terminated": {
                        "reason": "Error",
                        "exitCode": 3,
                        "finishedAt": "2026-10-22T23:29:00Z",
                    }
                },
            }
        ],
    },
}


def test_pod_summary_shows_why_a_pod_is_unhealthy():
    assert pod_summary(CRASHING_POD, NOW) == {
        "name": "payments-abc",
        "phase": "Running",
        "ready": "0/1",
        "restarts": 4,
        "age": "30m",
        "owner": "ReplicaSet",
        "waiting": "CrashLoopBackOff",
        "last_exit": {"reason": "Error", "exit_code": 3, "ago": "60s"},
    }


def kube(server: Recorder, tmp_path) -> list[Tool]:
    token = tmp_path / "token"
    token.write_text("secret-token\n")
    api = KubeApi("https://kube:443", token, server.client())
    return kubernetes_tools(api, "sentinel", clock=lambda: NOW)


def test_kubernetes_calls_use_the_service_account_token_and_only_get(tmp_path):
    server = Recorder(lambda r: httpx2.Response(200, json={"items": [CRASHING_POD]}))

    out = run_json(kube(server, tmp_path), "k8s_pods", {})

    assert out["pods"][0]["waiting"] == "CrashLoopBackOff"
    request = server.requests[0]
    assert request.method == "GET"
    assert request.url.path == "/api/v1/namespaces/sentinel/pods"
    assert request.headers["Authorization"] == "Bearer secret-token"


def test_forbidden_is_explained(tmp_path):
    server = Recorder(lambda r: httpx2.Response(403, json={"reason": "Forbidden"}))

    out = run(kube(server, tmp_path), "k8s_pods", {})

    assert out == "error: forbidden: the agent has no permission for this"


def test_events_filter_by_object_and_warnings_newest_first(tmp_path):
    def event(kind, reason, last):
        return {
            "type": kind,
            "reason": reason,
            "message": "m",
            "count": 2,
            "lastTimestamp": last,
            "involvedObject": {"kind": "Pod", "name": "payments-abc"},
            "metadata": {},
        }

    items = [
        event("Warning", "BackOff", "2026-10-22T23:20:00Z"),
        event("Normal", "Pulled", "2026-10-22T23:25:00Z"),
        event("Warning", "Unhealthy", "2026-10-22T23:28:00Z"),
    ]
    server = Recorder(lambda r: httpx2.Response(200, json={"items": items}))

    out = run_json(
        kube(server, tmp_path), "k8s_events", {"object_name": "payments-abc", "warnings_only": True}
    )

    assert [e["reason"] for e in out["events"]] == ["Unhealthy", "BackOff"]
    assert out["events"][0]["object"] == "Pod/payments-abc"
    assert server.requests[0].url.params["fieldSelector"] == "involvedObject.name=payments-abc"


def test_previous_container_logs(tmp_path):
    server = Recorder(lambda r: httpx2.Response(200, text="line 1\nValueError: abc\n"))

    out = run_json(
        kube(server, tmp_path), "k8s_pod_logs", {"pod": "payments-abc", "previous": True}
    )

    assert out["lines"] == ["line 1", "ValueError: abc"]
    params = server.requests[0].url.params
    assert (params["previous"], params["tailLines"]) == ("true", "50")
    assert server.requests[0].url.path == "/api/v1/namespaces/sentinel/pods/payments-abc/log"


def test_get_refuses_secrets_without_calling_the_api(tmp_path):
    server = Recorder(lambda r: httpx2.Response(200, json={}))

    out = run(kube(server, tmp_path), "k8s_get", {"kind": "secret"})

    assert out.startswith("error: kind must be one of: deployment,")
    assert server.requests == []


def test_get_one_object_without_bulky_fields(tmp_path):
    obj = {
        "metadata": {
            "name": "orders-db",
            "managedFields": [{"manager": "x"}] * 50,
            "annotations": {"cnpg.io/hibernation": "on"},
        },
        "status": {"phase": "Cluster in healthy state"},
    }
    server = Recorder(lambda r: httpx2.Response(200, json=obj))

    out = run_json(
        kube(server, tmp_path), "k8s_get", {"kind": "postgres-cluster", "name": "orders-db"}
    )

    assert "managedFields" not in out["metadata"]
    assert out["metadata"]["annotations"] == {"cnpg.io/hibernation": "on"}
    assert server.requests[0].url.path == (
        "/apis/postgresql.cnpg.io/v1/namespaces/sentinel/clusters/orders-db"
    )


def test_kubernetes_tools_say_when_not_in_a_cluster():
    tools = kubernetes_tools(None, "sentinel")
    assert run(tools, "k8s_pods", {}).startswith("error: not running inside Kubernetes")


# ---------- Calling tools ----------


def test_unknown_tool_and_bad_arguments_are_reported():
    tools = prometheus_tools(Recorder(prom_ok([])).client(), PROM)

    assert run(tools, "delete_everything", {}).startswith("error: unknown tool 'delete_everything'")
    assert run(tools, "prometheus_query", {}) == (
        "error: bad arguments for prometheus_query: query: Field required"
    )
    assert run(tools, "prometheus_range", {"query": "up", "minutes": 9999}).startswith(
        "error: bad arguments for prometheus_range: minutes:"
    )


def test_timeouts_are_reported():
    def slow(request):
        raise httpx2.ReadTimeout("slow", request=request)

    tools = prometheus_tools(Recorder(slow).client(), PROM)

    assert run(tools, "prometheus_query", {"query": "up"}) == "error: prometheus_query timed out"


def test_long_results_are_cut_with_a_note():
    text = to_text({"x": "a" * 100}, max_chars=20)  # 108 characters of JSON
    assert text.startswith('{"x":"aaaaaaaaaaaaaa')
    assert text.endswith("... [cut: 88 more characters]")


def test_tool_schema_is_a_function_definition():
    tool = prometheus_tools(Recorder(prom_ok([])).client(), PROM)[1]

    schema = tool.schema()

    assert schema["type"] == "function"
    assert schema["function"]["name"] == "prometheus_range"
    params = schema["function"]["parameters"]
    assert params["required"] == ["query"]
    assert params["properties"]["minutes"]["default"] == 30


@pytest.mark.parametrize("kind", ["secret", "secrets", "Secret"])
def test_secrets_are_never_a_kind(kind):
    from app.tools.kubernetes import KINDS

    assert kind not in KINDS
