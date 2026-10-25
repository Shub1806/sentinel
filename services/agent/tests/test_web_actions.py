from fastapi.testclient import TestClient

from app.actions import ActionError, why_not
from app.config import Settings
from app.incidents import IncidentStore
from app.investigator import Investigator
from app.main import create_app
from app.report import Report, SuggestedAction
from app.runner import InvestigationRunner
from tests.payloads import alert, notification

FORM = {"content-type": "application/x-www-form-urlencoded"}


class FakeExecutor:
    def __init__(self, fail=None):
        self.calls = []
        self.fail = fail

    async def run(self, kind, target, replicas=None):
        self.calls.append((kind, target, replicas))
        if self.fail:
            raise ActionError(self.fail)
        return f"did {kind} on {target}"


def setup(kind="rollout_undo", target="payments", replicas=None, fail=None):
    store = IncidentStore()
    runner = InvestigationRunner(Investigator({}, 6000, 4, 30, triage=False), lambda m: None)
    executor = FakeExecutor(fail)
    app = create_app(store, Settings(alertmanager_url=""), runner, executor=executor)
    client = TestClient(app)
    return client, store, executor, (kind, target, replicas)


def add_report(store, kind, target, replicas):
    incident = store.get(1)
    inv = store.new_investigation(incident, "m")
    inv.status = "done"
    inv.report = Report(
        summary="s",
        suggested_action=SuggestedAction(kind=kind, target=target, replicas=replicas),
    )
    return inv


def decide(client, inv_id, decision):
    return client.post(
        "/ui/incidents/1/actions",
        content=f"investigation={inv_id}&decision={decision}",
        headers=FORM,
        follow_redirects=False,
    )


def wait_for_actions(client):
    async def done():
        import asyncio

        while client.app.state.action_tasks:
            await asyncio.gather(*list(client.app.state.action_tasks))

    client.portal.call(done)


def test_buttons_show_then_approve_runs_the_action_once():
    client, store, executor, args = setup()
    with client:
        client.post("/alerts", json=notification(alert("payments")))
        inv = add_report(store, *args)
        page = client.get("/ui/incidents/1").text
        assert 'value="approve"' in page and 'value="reject"' in page

        response = decide(client, inv.id, "approve")
        wait_for_actions(client)
        again = decide(client, inv.id, "approve")
        page = client.get("/ui/incidents/1").text

    assert response.status_code == 303
    assert again.status_code == 409
    assert executor.calls == [("rollout_undo", "payments", None)]
    [action] = store.get(1).actions
    assert (action.status, action.result) == ("done", "did rollout_undo on payments")
    assert action.decided_by.startswith("page (")
    assert 'value="approve"' not in page
    assert "did rollout_undo on payments" in page


def test_reject_records_the_decision_and_runs_nothing():
    client, store, executor, args = setup()
    with client:
        client.post("/alerts", json=notification(alert("payments")))
        inv = add_report(store, *args)
        assert decide(client, inv.id, "reject").status_code == 303
        page = client.get("/ui/incidents/1").text

    assert executor.calls == []
    assert store.get(1).actions[0].status == "rejected"
    assert "rejected" in page


def test_a_failed_action_is_shown():
    client, store, _, args = setup(fail="forbidden: patch denied")
    with client:
        client.post("/alerts", json=notification(alert("payments")))
        inv = add_report(store, *args)
        decide(client, inv.id, "approve")
        wait_for_actions(client)
    action = store.get(1).actions[0]
    assert (action.status, action.result) == ("failed", "forbidden: patch denied")


def test_targets_off_the_list_cannot_be_approved():
    client, store, executor, _ = setup()
    with client:
        client.post("/alerts", json=notification(alert("payments")))
        inv = add_report(store, "restart_deployment", "redpanda", None)
        page = client.get("/ui/incidents/1").text
        response = decide(client, inv.id, "approve")

    assert 'value="approve"' not in page
    assert "redpanda is not on the list" in page
    assert response.status_code == 400
    assert executor.calls == []


def test_manual_suggestions_have_no_buttons():
    client, store, _, _ = setup()
    with client:
        client.post("/alerts", json=notification(alert("payments")))
        inv = add_report(store, "manual", "", None)
        page = client.get("/ui/incidents/1").text
        assert decide(client, inv.id, "approve").status_code == 400
    assert "Nothing for the agent to carry out." in page
    assert why_not("manual", "", None, ("payments",)) is None


def test_bad_form_values_are_rejected():
    client, store, _, args = setup()
    with client:
        client.post("/alerts", json=notification(alert("payments")))
        inv = add_report(store, *args)
        assert decide(client, inv.id, "maybe").status_code == 400
        assert decide(client, 999, "approve").status_code == 400
        assert client.post("/ui/incidents/7/actions", content="", headers=FORM).status_code == 404
