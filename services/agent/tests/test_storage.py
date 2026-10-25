from datetime import UTC, datetime

from app.alerts import Notification
from app.incidents import IncidentStore
from app.records import Step
from app.report import Report
from app.storage import Database
from tests.payloads import alert, notification


def parse(body):
    return Notification.model_validate(body)


def test_incidents_and_investigations_survive_a_restart(tmp_path):
    path = str(tmp_path / "data" / "agent.db")  # the folder is created if missing
    store = IncidentStore(db=Database(path))
    incident, _ = store.record(parse(notification(alert("payments"))))
    inv = store.new_investigation(incident, "qwen3:4b")
    inv.status = "done"
    inv.started_at = datetime(2026, 10, 24, 22, 0, tzinfo=UTC)
    inv.steps.append(Step("k8s_pods", {}, '{"pods":[]}', 0.1, auto=True))
    inv.report = Report(summary="s", likely_cause="c", evidence=["e"], confidence="high")
    store.save_investigation(inv)

    reloaded = IncidentStore(db=Database(path))

    [again] = reloaded.all()
    assert again.title == "InvalidMessagesReceived"
    assert again.status == "firing"
    assert again.alerts["fp-payments"].starts_at == incident.alerts["fp-payments"].starts_at
    [saved] = again.investigations
    assert (saved.model, saved.status, saved.report.likely_cause) == ("qwen3:4b", "done", "c")
    assert saved.steps[0] == Step("k8s_pods", {}, '{"pods":[]}', 0.1, auto=True)
    assert saved.started_at == inv.started_at


def test_numbering_continues_and_open_incidents_still_match(tmp_path):
    path = str(tmp_path / "agent.db")
    store = IncidentStore(db=Database(path))
    first, _ = store.record(parse(notification(alert("payments"))))
    store.new_investigation(first, "m")

    reloaded = IncidentStore(db=Database(path))
    # The same alert group after a restart updates the reloaded incident.
    same, opened = reloaded.record(parse(notification(alert("payments"), alert("notifications"))))
    other, _ = reloaded.record(parse(notification(alert("orders"), group="OutboxBacklog")))

    assert not opened and same.id == 1 and same.firing_alerts == 2
    assert other.id == 2
    assert reloaded.new_investigation(other, "m").id == 2


def test_an_investigation_cut_off_by_a_restart_is_marked_failed(tmp_path):
    path = str(tmp_path / "agent.db")
    store = IncidentStore(db=Database(path))
    incident, _ = store.record(parse(notification(alert("payments"))))
    inv = store.new_investigation(incident, "m")
    inv.status = "running"
    store.save_investigation(inv)

    [again] = IncidentStore(db=Database(path)).all()

    assert again.investigations[0].status == "failed"
    assert again.investigations[0].error == "the agent restarted before it finished"


def test_resolved_incidents_stay_resolved(tmp_path):
    path = str(tmp_path / "agent.db")
    store = IncidentStore(db=Database(path))
    store.record(parse(notification(alert("payments"))))
    store.record(parse(notification(alert("payments", "resolved", "2026-10-22T23:25:00Z"))))

    reloaded = IncidentStore(db=Database(path))
    _, opened = reloaded.record(parse(notification(alert("payments"))))

    assert reloaded.get(1).status == "resolved"
    assert opened  # firing again later is a new incident


def test_actions_survive_a_restart_and_a_running_one_is_marked_failed(tmp_path):
    path = str(tmp_path / "agent.db")
    store = IncidentStore(db=Database(path))
    incident, _ = store.record(parse(notification(alert("payments"))))
    inv = store.new_investigation(incident, "m")
    inv.report = Report(summary="s")
    done = store.new_action(incident, inv, status="done", result="ok")
    store.new_action(incident, inv, status="running")

    reloaded = IncidentStore(db=Database(path))
    first, second = reloaded.get(1).actions

    assert (first.id, first.status, first.result) == (done.id, "done", "ok")
    assert second.status == "failed" and "restarted while the action ran" in second.result
    assert reloaded.new_action(reloaded.get(1), inv).id == 3
