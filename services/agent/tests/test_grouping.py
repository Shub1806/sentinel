from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.alerts import Notification
from app.config import Settings
from app.grouping import deployment_of, subjects, workload_of
from app.incidents import IncidentStore
from app.investigator import Investigator
from app.llm import Reply
from app.main import create_app
from app.runner import InvestigationRunner
from app.storage import Database

POD = "payments-58f84654df-plx78"
KSM = {"job": "kube-state-metrics", "service": "monitoring-kube-state-metrics"}
# The alerts tonight's bad payments rollout fired, with the labels they really carry.
BAD_ROLLOUT = [
    ("PodRestarted", {"pod": POD}),
    ("KubePodCrashLooping", {"pod": POD, "container": "payments", **KSM}),
    ("KubePodNotReady", {"pod": POD, **KSM}),
    ("TargetDown", {"job": "payments", "service": "payments"}),
    ("KubeHpaMaxedOut", {"horizontalpodautoscaler": "payments", **KSM}),
]


@pytest.mark.parametrize(
    ("pod", "workload", "deployment"),
    [
        (POD, "payments", "payments"),
        ("payments-dd74755d-g7hst", "payments", "payments"),
        ("orders-db-1", "orders-db", None),  # Postgres, not the orders service
        ("redpanda-0", "redpanda", None),
        ("redpanda-configure-pmckn", "redpanda-configure", None),
        ("redpanda-create-topics-q9flv", "redpanda-create-topics", None),
    ],
)
def test_pod_names(pod, workload, deployment):
    assert workload_of(pod) == workload
    assert deployment_of(pod) == deployment


def test_subjects_ignore_the_exporter_that_produced_the_metric():
    for _, labels in BAD_ROLLOUT:
        assert subjects(labels) == {"payments"}
    assert subjects({"redpanda_group": "notifications"}) == {"notifications"}
    assert subjects({"service": "orders"}) == {"orders"}
    assert subjects(KSM) == set()


def notify(name, labels, status="firing"):
    return Notification.model_validate(
        {
            "version": "4",
            "groupKey": name,
            "status": status,
            "receiver": "agent",
            "groupLabels": {"alertname": name, "namespace": "sentinel"},
            "alerts": [
                {
                    "status": status,
                    "labels": {"alertname": name, "namespace": "sentinel", **labels},
                    "startsAt": "2026-10-25T03:19:35Z",
                    "endsAt": "2026-10-25T03:31:35Z" if status == "resolved" else None,
                    "fingerprint": name,
                }
            ],
        }
    )


class Clock:
    def __init__(self):
        self.now = datetime(2026, 10, 25, 3, 20, tzinfo=UTC)

    def __call__(self):
        return self.now


def test_one_bad_rollout_becomes_one_incident():
    store = IncidentStore()
    for name, labels in BAD_ROLLOUT:
        store.record(notify(name, labels))

    [top] = store.top_level()

    assert top.title == "PodRestarted"
    assert [i.title for i in store.related(top)] == [n for n, _ in BAD_ROLLOUT[1:]]
    assert all(i.parent_id == top.id for i in store.related(top))


def test_other_services_and_old_or_resolved_incidents_stay_separate():
    clock = Clock()
    store = IncidentStore(clock=clock)
    first, _ = store.record(notify("PodRestarted", {"pod": POD}))

    orders, _ = store.record(notify("OutboxBacklog", {"job": "orders"}))
    clock.now += timedelta(minutes=31)
    late, _ = store.record(notify("TargetDown", {"job": "payments"}))
    store.record(notify("PodRestarted", {"pod": POD}, "resolved"))
    after, _ = store.record(notify("KubePodNotReady", {"pod": POD, **KSM}))

    assert orders.parent_id is None  # different service
    assert late.parent_id is None  # first incident opened more than 30 min earlier
    assert after.parent_id == late.id  # joins the newer open incident, not the resolved one
    assert first.status == "resolved"


def test_group_resolves_only_when_every_member_has(tmp_path):
    store = IncidentStore(db=Database(str(tmp_path / "agent.db")))
    top, _ = store.record(notify("PodRestarted", {"pod": POD}))
    store.record(notify("TargetDown", {"job": "payments"}))

    store.record(notify("PodRestarted", {"pod": POD}, "resolved"))
    assert not store.group_resolved(top)
    store.record(notify("TargetDown", {"job": "payments"}, "resolved"))
    assert store.group_resolved(top)

    reloaded = IncidentStore(db=Database(str(tmp_path / "agent.db")))
    assert reloaded.get(2).parent_id == 1


class FixedModel:
    def __init__(self, name):
        self.model = name

    async def chat(self, messages, tools=None):
        if tools is None and "post-mortem" in messages[0]["content"]:
            return Reply('{"what_happened": "x", "impact": "y", "follow_ups": []}', [])
        return Reply('{"summary": "s", "likely_cause": "c"}', [])


def body(name, labels, status="firing"):
    return notify(name, labels, status).model_dump(by_alias=True, mode="json")


def test_only_the_first_is_investigated_and_one_post_mortem_covers_the_group():
    store = IncidentStore()
    used = []
    runner = InvestigationRunner(
        Investigator({}, 6000, 4, 30, triage=False), lambda m: used.append(m) or FixedModel(m)
    )
    app = create_app(store, Settings(llm_model="m", alertmanager_url=""), runner)
    with TestClient(app) as client:
        for name, labels in BAD_ROLLOUT:
            client.post("/alerts", json=body(name, labels))
        client.portal.call(runner.wait)
        listing = client.get("/").text
        child_page = client.get("/ui/incidents/3").text
        parent_page = client.get("/ui/incidents/1").text
        assert client.post("/ui/incidents/3/postmortem").status_code == 409

        for name, labels in BAD_ROLLOUT[:-1]:
            client.post("/alerts", json=body(name, labels, "resolved"))
        client.portal.call(runner.wait)
        assert store.get(1).postmortem is None  # KubeHpaMaxedOut still firing
        client.post("/alerts", json=body(*BAD_ROLLOUT[-1], "resolved"))
        client.portal.call(runner.wait)
        md = client.get("/incidents/1/postmortem.md").text

    assert [i.id for i in store.all() if i.investigations] == [1]
    assert (
        "+4 related (KubePodCrashLooping, KubePodNotReady, TargetDown, KubeHpaMaxedOut)" in listing
    )
    assert 'href="/ui/incidents/3"' not in listing  # related incidents are not listed on their own
    assert 'Grouped into <a href="/ui/incidents/1">#1 PodRestarted</a>' in child_page
    assert "(payments)" in child_page
    assert "Related incidents (4)" in parent_page
    assert "## Related alerts" in md
    assert "alert KubeHpaMaxedOut fired (payments, related #5)" in md
    assert "alert TargetDown fired (payments, related #4)" in md
    assert "kube-state-metrics" not in md
    assert all(i.postmortem is None for i in store.related(store.get(1)))
