import asyncio
import json

import pytest
from prometheus_client import REGISTRY

from app.config import Settings
from app.worker import PaymentsWorker


def order_json(amount_cents: int = 1999) -> bytes:
    return json.dumps(
        {
            "event_id": "11111111-1111-1111-1111-111111111111",
            "event_type": "order.created",
            "occurred_at": "2026-10-15T00:00:00Z",
            "order_id": "22222222-2222-2222-2222-222222222222",
            "customer_id": "cust-1",
            "amount_cents": amount_cents,
            "currency": "USD",
        }
    ).encode()


VALID = order_json()


class FakeProducer:
    """Stands in for Kafka: just records what would have been sent."""

    def __init__(self):
        self.sent = []

    async def send_and_wait(self, topic, value, key=None):
        self.sent.append((topic, key, json.loads(value)))


class BrokenProducer:
    """Stands in for Kafka when the broker is unreachable."""

    async def send_and_wait(self, topic, value, key=None):
        raise ConnectionError("broker unreachable")


def make_worker() -> PaymentsWorker:
    return PaymentsWorker(Settings(kafka_bootstrap_servers="unused:1"))


def metric(name: str, **labels: str) -> float:
    """Current value of a metric. Metrics are shared by all tests, so tests compare before/after."""
    return REGISTRY.get_sample_value(name, labels) or 0.0


def test_valid_message_is_published():
    producer = FakeProducer()
    result = asyncio.run(make_worker().handle(VALID, producer))

    assert result is not None
    topic, key, body = producer.sent[0]
    assert topic == "payment.done"
    assert key == b"22222222-2222-2222-2222-222222222222"
    assert body["event_type"] == "payment.done"
    assert body["status"] == "succeeded"
    assert body["order_id"] == "22222222-2222-2222-2222-222222222222"


def test_invalid_message_is_skipped():
    producer = FakeProducer()
    result = asyncio.run(make_worker().handle(b"not json", producer))

    assert result is None
    assert producer.sent == []


def test_successful_payment_is_counted_and_timed():
    succeeded = metric("payments_processed_total", status="succeeded")
    timed = metric("payments_processing_seconds_count")

    asyncio.run(make_worker().handle(VALID, FakeProducer()))

    assert metric("payments_processed_total", status="succeeded") == succeeded + 1
    assert metric("payments_processing_seconds_count") == timed + 1


def test_failed_payment_is_counted_as_failed():
    failed = metric("payments_processed_total", status="failed")
    succeeded = metric("payments_processed_total", status="succeeded")

    # Far above the default limit, so the charge fails.
    result = asyncio.run(make_worker().handle(order_json(amount_cents=10_000_000), FakeProducer()))

    assert result is not None and result.status == "failed"
    assert metric("payments_processed_total", status="failed") == failed + 1
    assert metric("payments_processed_total", status="succeeded") == succeeded


def test_invalid_message_is_counted_but_not_as_a_payment():
    invalid = metric("payments_invalid_messages_total")
    succeeded = metric("payments_processed_total", status="succeeded")

    asyncio.run(make_worker().handle(b"not json", FakeProducer()))

    assert metric("payments_invalid_messages_total") == invalid + 1
    assert metric("payments_processed_total", status="succeeded") == succeeded


def test_payment_that_could_not_be_published_is_not_counted():
    succeeded = metric("payments_processed_total", status="succeeded")

    with pytest.raises(ConnectionError):
        asyncio.run(make_worker().handle(VALID, BrokenProducer()))

    assert metric("payments_processed_total", status="succeeded") == succeeded
