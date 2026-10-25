import json

from fastapi.testclient import TestClient

from app import rules
from app.alerts import Notification
from app.config import Settings
from app.incidents import IncidentStore
from app.investigator import Investigator
from app.main import create_app
from app.records import Step
from app.report import Report, SuggestedAction
from app.runner import InvestigationRunner
from app.storage import Database
from tests.payloads import alert, notification


def diff_step(new_ready, old_ready, changes, healthy):
    result = {
        "deployment": "payments",
        "newest": {"revision": 4, "created": "2m ago", "replicas": 1, "ready": new_ready},
        "previous": {"revision": 3, "created": "1h ago", "replicas": 1, "ready": old_ready},
        "changes": changes,
        "new_pods_healthy": healthy,
    }
    return Step("k8s_rollout_diff", {"deployment": "payments"}, json.dumps(result), 0.0, True)


def incident(name="PodRestarted"):
    body = notification(alert("payments"), group=name)
    body["alerts"][0]["labels"]["alertname"] = name
    store = IncidentStore()
    inc, _ = store.record(Notification.model_validate(body))
    return inc


BAD = ["payments: env MAX_AMOUNT_CENTS added = abc"]


def test_bad_rollout_is_a_strong_rollout_undo():
    v = rules.evaluate(incident(), [diff_step(0, 1, BAD, False)])
    assert v == {
        "rule": "bad rollout",
        "kind": "rollout_undo",
        "target": "payments",
        "cause": "the latest rollout of payments changed: payments: env MAX_AMOUNT_CENTS added = "
        "abc; its new pods are not ready (0/1) while revision 3 was (1/1)",
        "strong": True,
    }


def test_healthy_or_unchanged_rollouts_are_not_blamed():
    inc = incident("InvalidMessagesReceived")
    healthy = rules.evaluate(inc, [diff_step(1, 0, ["ConfigMap settings changed"], True)])
    unchanged = rules.evaluate(
        inc, [diff_step(0, 1, ["no differences in the pod template"], False)]
    )
    for v in (healthy, unchanged):
        assert v["rule"] == "alert InvalidMessagesReceived"
        assert (v["kind"], v["strong"]) == ("none", False)


def test_previous_revision_also_broken_is_not_a_bad_rollout():
    assert rules.evaluate(incident(), [diff_step(0, 0, BAD, False)]) is None


def test_errors_and_unknown_alerts_give_no_verdict():
    broken = Step("k8s_rollout_diff", {}, "error: forbidden", 0.0, True)
    assert rules.evaluate(incident(), [broken]) is None
    assert rules.evaluate(incident("SomethingElse"), []) is None


def test_only_a_strong_different_verdict_counts_as_disagreement():
    strong = rules.evaluate(incident(), [diff_step(0, 1, BAD, False)])
    hint = rules.evaluate(incident("OutboxBacklog"), [])
    assert rules.disagrees(strong, "restart_deployment", "payments")
    assert not rules.disagrees(strong, "rollout_undo", "payments")
    assert not rules.disagrees(hint, "restart_deployment", "orders")
    assert not rules.disagrees(None, "none", "")


def page_with(model_kind, tmp_path):
    db = Database(str(tmp_path / "agent.db"))
    store = IncidentStore(db=db)
    runner = InvestigationRunner(Investigator({}, 6000, 4, 30, triage=False), lambda m: None)
    app = create_app(store, Settings(alertmanager_url=""), runner)
    with TestClient(app) as client:
        client.post("/alerts", json=notification(alert("payments")))
        inv = store.new_investigation(store.get(1), "m")
        inv.status = "done"
        inv.steps.append(diff_step(0, 1, BAD, False))
        inv.rule_verdict = rules.evaluate(store.get(1), inv.steps)
        inv.report = Report(
            summary="s", suggested_action=SuggestedAction(kind=model_kind, target="payments")
        )
        store.save_investigation(inv)
        page = client.get("/ui/incidents/1").text
        detail = client.get("/incidents/1").json()
    return page, detail, IncidentStore(db=Database(str(tmp_path / "agent.db")))


def test_page_warns_when_the_rules_disagree(tmp_path):
    page, detail, reloaded = page_with("restart_deployment", tmp_path)
    assert "The fixed rules disagree:" in page
    assert "rollout_undo <code>payments</code>, because the latest rollout" in page
    assert detail["investigations"][0]["rule_verdict"]["rule"] == "bad rollout"
    assert reloaded.get(1).investigations[0].rule_verdict["strong"] is True  # saved


def test_page_shows_agreement_quietly(tmp_path):
    page, _, _ = page_with("rollout_undo", tmp_path)
    assert "The fixed rules disagree" not in page
    assert "fixed rules (bad rollout): rollout_undo" in page
