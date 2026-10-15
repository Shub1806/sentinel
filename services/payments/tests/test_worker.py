import asyncio
import json

from app.config import Settings
from app.worker import PaymentsWorker

VALID = json.dumps(
    {
        "event_id": "11111111-1111-1111-1111-111111111111",
        "event_type": "order.created",
        "occurred_at": "2026-10-15T00:00:00Z",
        "order_id": "22222222-2222-2222-2222-222222222222",
        "customer_id": "cust-1",
        "amount_cents": 1999,
        "currency": "USD",
    }
).encode()


class FakeProducer:
    """Stands in for Kafka: just records what would have been sent."""

    def __init__(self):
        self.sent = []

    async def send_and_wait(self, topic, value, key=None):
        self.sent.append((topic, key, json.loads(value)))


def make_worker() -> PaymentsWorker:
    return PaymentsWorker(Settings(kafka_bootstrap_servers="unused:1"))


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
