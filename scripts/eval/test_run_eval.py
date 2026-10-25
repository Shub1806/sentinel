"""Tests for the evaluation harness, with a fake agent, Prometheus and shell.
Run from the repo root: python3 -m pytest -q scripts/eval"""

import json

import pytest
import run_eval
from scenarios import SCENARIOS, action_ok, cause_ok


def test_scoring_rules():
    bad = SCENARIOS["bad-deploy"]
    assert cause_ok(bad, "Env MAX_AMOUNT_CENTS set to 'abc'")
    assert not cause_ok(bad, "Redpanda restart")
    assert action_ok(bad, "rollout_undo", "payments")
    assert not action_ok(bad, "restart_deployment", "payments")
    assert not action_ok(bad, "rollout_undo", "orders")
    poison = SCENARIOS["poison"]
    assert action_ok(poison, "none", "") and action_ok(poison, "manual", "anything")


class Fake:
    """Plays the cluster: alerts in Prometheus, incidents in the agent, chaos scripts."""

    def __init__(self, inv):
        self.calls = []
        self.alerts = [
            [{"labels": {"alertname": "PodRestarted", "namespace": "sentinel"}, "state": "firing"}],
            [],
        ]
        self.polls = 0
        self.inv = inv

    def get_json(self, url):
        if url.endswith("/api/v1/alerts"):
            alerts = self.alerts.pop(0) if len(self.alerts) > 1 else self.alerts[0]
            return {"data": {"alerts": alerts}}
        if url.endswith("/incidents"):
            self.polls += 1
            old = [{"id": 1, "title": "PodRestarted"}]
            new = (
                [{"id": 2, "title": "KubePodNotReady", "grouped_into": 3}] if self.polls > 2 else []
            )
            return new + old
        if url.endswith("/incidents/2"):
            return {"id": 2, "grouped_into": 3, "investigations": []}
        if url.endswith("/incidents/3"):
            return {"id": 3, "grouped_into": None, "investigations": [self.inv]}
        raise AssertionError(url)

    def sh(self, *args, check=True):
        self.calls.append(" ".join(args))


@pytest.fixture
def fake(monkeypatch, tmp_path):
    inv = {
        "status": "done",
        "finished_at": "2099-01-01T00:00:00+00:00",
        "report": {
            "summary": "payments crash-loops",
            "likely_cause": "MAX_AMOUNT_CENTS=abc in the latest rollout",
            "suggested_action": {"kind": "restart_deployment", "target": "payments"},
        },
        "rule_verdict": {
            "rule": "bad rollout",
            "kind": "rollout_undo",
            "target": "payments",
            "cause": "rollout added MAX_AMOUNT_CENTS = abc",
            "strong": True,
        },
    }
    f = Fake(inv)
    monkeypatch.setattr(run_eval, "get_json", f.get_json)
    monkeypatch.setattr(run_eval, "sh", f.sh)
    monkeypatch.setattr(run_eval, "RESULTS", tmp_path)
    monkeypatch.setattr(run_eval.time, "sleep", lambda s: None)
    return f


def test_one_run_waits_injects_follows_the_group_scores_and_recovers(fake, tmp_path):
    scenario = SCENARIOS["bad-deploy"]
    result = run_eval.run_once(scenario, 1)

    assert fake.calls[0].startswith("kubectl delete -f")  # no loadgen for this scenario
    assert fake.calls[1] == "scripts/chaos/payments-bad-config.sh inject"
    assert fake.calls[-1] == "scripts/chaos/payments-bad-config.sh recover"
    assert result["incident"]["id"] == 2  # the new KubePodNotReady incident ...
    assert (
        result["detail"]["id"] == 3
    )  # ... was grouped; its group's first incident was investigated
    assert result["agent_cause_ok"] and not result["agent_action_ok"]
    assert result["agent_action"] == "restart_deployment payments"
    assert result["baseline_cause_ok"] and result["baseline_action_ok"]
    assert json.loads((tmp_path / "bad-deploy-1.json").read_text())["run"] == 1

    # A second call resumes: the saved run is not repeated.
    calls = len(fake.calls)
    run_eval.run_once(scenario, 1)
    assert len(fake.calls) == calls


def test_report_table(fake, tmp_path):
    run_eval.run_once(SCENARIOS["bad-deploy"], 1)
    text = run_eval.report()
    assert (
        "| Bad deploy: payments crash-loops | 1 | 100% (1/1) | 0% (0/1) | 100% (1/1) | 100% (1/1) |"
        in text
    )
    assert "| bad-deploy-1 | restart_deployment payments | rollout_undo payments |  |" in text
    assert (tmp_path / "summary.md").exists()


def test_no_report_before_the_deadline_is_a_timeout(fake, monkeypatch, tmp_path):
    fake.inv["status"] = "running"
    clock = iter(range(0, 10_000, 30))
    monkeypatch.setattr(run_eval.time, "time", lambda: next(clock))
    result = run_eval.run_once(SCENARIOS["poison"], 1)
    assert result["timed_out"] and not result["agent_cause_ok"]
    assert result["baseline_action"] == "(no rule)"
