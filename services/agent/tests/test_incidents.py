from datetime import UTC, datetime, timedelta

from app.alerts import Notification
from app.incidents import IncidentStore
from tests.payloads import alert, notification


class Clock:
    def __init__(self):
        self.now = datetime(2026, 10, 22, 23, 15, tzinfo=UTC)

    def __call__(self):
        return self.now

    def tick(self, minutes: int = 1):
        self.now += timedelta(minutes=minutes)


def parse(body: dict) -> Notification:
    return Notification.model_validate(body)


def test_first_firing_notification_opens_an_incident():
    store = IncidentStore()

    incident, opened = store.record(parse(notification(alert("payments"))))

    assert opened
    assert incident.id == 1
    assert incident.title == "InvalidMessagesReceived"
    assert incident.status == "firing"
    assert incident.firing_alerts == 1
    assert store.open_count() == 1


def test_later_notifications_for_the_same_group_update_the_incident():
    store = IncidentStore()
    store.record(parse(notification(alert("payments"))))

    incident, opened = store.record(parse(notification(alert("payments"), alert("notifications"))))

    assert not opened
    assert incident.id == 1
    assert incident.firing_alerts == 2
    assert incident.notifications == 2
    assert len(store.all()) == 1


def test_incident_resolves_only_when_every_alert_is_resolved():
    clock = Clock()
    store = IncidentStore(clock=clock)
    store.record(parse(notification(alert("payments"), alert("notifications"))))

    clock.tick()
    resolved_at = "2026-10-22T23:25:00Z"
    incident, _ = store.record(
        parse(notification(alert("payments", "resolved", resolved_at), alert("notifications")))
    )
    assert incident.status == "firing"
    assert incident.alerts["fp-payments"].ends_at is not None

    clock.tick()
    incident, _ = store.record(
        parse(
            notification(
                alert("payments", "resolved", resolved_at),
                alert("notifications", "resolved", resolved_at),
            )
        )
    )
    assert incident.status == "resolved"
    assert incident.resolved_at == clock.now
    assert store.open_count() == 0


def test_same_group_firing_again_after_resolution_is_a_new_incident():
    store = IncidentStore()
    store.record(parse(notification(alert("payments"))))
    store.record(parse(notification(alert("payments", "resolved", "2026-10-22T23:25:00Z"))))

    incident, opened = store.record(parse(notification(alert("payments"))))

    assert opened
    assert incident.id == 2
    assert [i.id for i in store.all()] == [2, 1]  # newest first


def test_different_groups_are_different_incidents():
    store = IncidentStore()
    store.record(parse(notification(alert("payments"))))

    incident, opened = store.record(parse(notification(alert("orders"), group="OutboxBacklog")))

    assert opened
    assert incident.title == "OutboxBacklog"
    assert store.open_count() == 2


def test_firing_alert_has_no_end_time():
    store = IncidentStore()
    incident, _ = store.record(parse(notification(alert("payments"))))
    assert incident.alerts["fp-payments"].ends_at is None


def test_resolved_notification_without_an_open_incident_is_kept_as_resolved():
    # For example, the agent restarted while the alert was firing.
    store = IncidentStore()

    incident, opened = store.record(
        parse(notification(alert("payments", "resolved", "2026-10-22T23:25:00Z")))
    )

    assert opened
    assert incident.status == "resolved"
    assert store.open_count() == 0


def test_incidents_are_matched_by_group_labels_not_group_key():
    store = IncidentStore()
    first = notification(alert("payments"))
    later = notification(alert("payments"))
    later["groupKey"] = "something-else"  # e.g. rebuilt after an agent restart

    store.record(parse(first))
    incident, opened = store.record(parse(later))

    assert not opened
    assert incident.group_key == "alertname=InvalidMessagesReceived,namespace=sentinel"
