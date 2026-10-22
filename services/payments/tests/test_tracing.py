import asyncio
import json

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind, StatusCode

from app.config import Settings
from app.tracing import build_provider, configure_tracing
from app.worker import PaymentsWorker

# What orders puts on an order.created message (trace id, then the id of its send span).
ORDERS_TRACE_ID = "4bf92f3577b34da6a3ce929d0e0e4736"
ORDERS_SPAN_ID = "00f067aa0ba902b7"
ORDERS_HEADERS = [("traceparent", f"00-{ORDERS_TRACE_ID}-{ORDERS_SPAN_ID}-03".encode())]

ORDER = json.dumps(
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


class RecordingProducer:
    def __init__(self):
        self.headers = []

    async def send_and_wait(self, topic, value, key=None, headers=None):
        self.headers.append(dict(headers or []))


class BrokenProducer:
    async def send_and_wait(self, topic, value, key=None, headers=None):
        raise ConnectionError("broker unreachable")


@pytest.fixture
def spans():
    """A real tracer that keeps finished spans in memory."""
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    yield exporter, provider.get_tracer("test")
    provider.shutdown()


def run(worker, raw, producer, headers=()):
    return asyncio.run(worker.handle(raw, producer, headers))


def by_kind(exporter, kind):
    return [s for s in exporter.get_finished_spans() if s.kind == kind]


def test_processing_continues_the_trace_from_orders(spans):
    exporter, tracer = spans
    worker = PaymentsWorker(Settings(kafka_bootstrap_servers="unused:1"), tracer=tracer)

    run(worker, ORDER, RecordingProducer(), ORDERS_HEADERS)

    (consumer,) = by_kind(exporter, SpanKind.CONSUMER)
    assert consumer.name == "order.created process"
    assert format(consumer.context.trace_id, "032x") == ORDERS_TRACE_ID
    assert format(consumer.parent.span_id, "016x") == ORDERS_SPAN_ID
    assert consumer.attributes["order.id"] == "22222222-2222-2222-2222-222222222222"


def test_payment_done_carries_the_trace_onward(spans):
    exporter, tracer = spans
    worker = PaymentsWorker(Settings(kafka_bootstrap_servers="unused:1"), tracer=tracer)
    producer = RecordingProducer()

    run(worker, ORDER, producer, ORDERS_HEADERS)

    (consumer,) = by_kind(exporter, SpanKind.CONSUMER)
    (send,) = by_kind(exporter, SpanKind.PRODUCER)
    assert send.name == "payment.done send"
    assert send.parent.span_id == consumer.context.span_id
    # notifications continues from the send span, in the same trace.
    traceparent = producer.headers[0]["traceparent"].decode()
    _, trace_id, parent_id, _ = traceparent.split("-")
    assert trace_id == ORDERS_TRACE_ID
    assert parent_id == format(send.context.span_id, "016x")


def test_message_without_trace_starts_a_new_one(spans):
    exporter, tracer = spans
    worker = PaymentsWorker(Settings(kafka_bootstrap_servers="unused:1"), tracer=tracer)
    producer = RecordingProducer()

    run(worker, ORDER, producer)

    (consumer,) = by_kind(exporter, SpanKind.CONSUMER)
    assert consumer.parent is None
    assert "traceparent" in producer.headers[0]


def test_invalid_message_marks_the_span_as_failed(spans):
    exporter, tracer = spans
    worker = PaymentsWorker(Settings(kafka_bootstrap_servers="unused:1"), tracer=tracer)
    producer = RecordingProducer()

    run(worker, b"not json", producer, ORDERS_HEADERS)

    (consumer,) = by_kind(exporter, SpanKind.CONSUMER)
    assert consumer.status.status_code == StatusCode.ERROR
    assert format(consumer.context.trace_id, "032x") == ORDERS_TRACE_ID
    assert producer.headers == []


def test_broker_failure_is_recorded_on_the_spans(spans):
    exporter, tracer = spans
    worker = PaymentsWorker(Settings(kafka_bootstrap_servers="unused:1"), tracer=tracer)

    with pytest.raises(ConnectionError):
        run(worker, ORDER, BrokenProducer(), ORDERS_HEADERS)

    (send,) = by_kind(exporter, SpanKind.PRODUCER)
    assert send.status.status_code == StatusCode.ERROR
    assert any(event.name == "exception" for event in send.events)


def test_tracing_stays_off_without_an_endpoint():
    assert configure_tracing({}) is None
    assert configure_tracing({"OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": "  "}) is None


def test_provider_names_the_service():
    provider = build_provider("http://localhost:4318/v1/traces", "payments")
    try:
        assert provider.resource.attributes["service.name"] == "payments"
    finally:
        provider.shutdown()
