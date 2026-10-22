"""Reads order.created, charges each order, publishes payment.done."""

import asyncio
import logging
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Protocol

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from opentelemetry import propagate, trace
from opentelemetry.trace import SpanKind, Status, StatusCode, Tracer
from pydantic import ValidationError

from app.config import Settings
from app.metrics import INVALID_MESSAGES, PAYMENTS_PROCESSED, PROCESSING_SECONDS
from app.models import OrderCreated, PaymentDone
from app.payment_logic import charge

log = logging.getLogger("payments.worker")

# Kafka message headers: (name, value) pairs with bytes values.
Headers = Sequence[tuple[str, bytes]]


class Producer(Protocol):
    async def send_and_wait(
        self, topic: str, value: bytes, key: bytes | None = None, headers: Headers | None = None
    ): ...


def _trace_parent_from(headers: Headers):
    """The trace context that orders put in the message headers (empty if there is none)."""
    carrier = {name: value.decode("utf-8", "replace") for name, value in headers}
    return propagate.extract(carrier)


def _trace_headers() -> list[tuple[str, bytes]]:
    """The current trace context as Kafka headers, so the next service can continue the trace."""
    carrier: dict[str, str] = {}
    propagate.inject(carrier)
    return [(name, value.encode()) for name, value in carrier.items()]


class PaymentsWorker:
    def __init__(self, settings: Settings, tracer: Tracer | None = None) -> None:
        self.settings = settings
        self.ready = False  # True once connected to Redpanda
        self.tracer = tracer or trace.get_tracer("payments.worker")

    async def handle(
        self, raw: bytes, producer: Producer, headers: Headers = ()
    ) -> PaymentDone | None:
        """Process one message, inside the trace it arrived with. Returns the published
        event, or None if skipped."""
        topic = self.settings.input_topic
        with self.tracer.start_as_current_span(
            f"{topic} process",
            context=_trace_parent_from(headers),
            kind=SpanKind.CONSUMER,
            attributes={"messaging.system": "kafka", "messaging.destination.name": topic},
        ) as span:
            return await self._process(raw, producer, span)

    async def _process(self, raw: bytes, producer: Producer, span) -> PaymentDone | None:
        try:
            order = OrderCreated.model_validate_json(raw)
        except ValidationError as exc:
            # A bad message must not stop the whole service. Log it and move on.
            INVALID_MESSAGES.inc()
            span.set_status(Status(StatusCode.ERROR, "invalid order.created message"))
            log.warning("skipping invalid order.created message", extra={"errors": exc.errors()})
            return None
        span.set_attribute("order.id", str(order.order_id))

        # Measures charging plus publishing, including any artificial delay.
        with PROCESSING_SECONDS.time():
            if self.settings.processing_delay_ms:
                await asyncio.sleep(self.settings.processing_delay_ms / 1000)

            payment = charge(
                order, max_amount_cents=self.settings.max_amount_cents, now=datetime.now(UTC)
            )
            out = self.settings.output_topic
            with self.tracer.start_as_current_span(
                f"{out} send",
                kind=SpanKind.PRODUCER,
                attributes={"messaging.system": "kafka", "messaging.destination.name": out},
            ):
                await producer.send_and_wait(
                    out,
                    value=payment.model_dump_json().encode(),
                    key=str(payment.order_id).encode(),
                    headers=_trace_headers(),
                )

        # Count only after publishing succeeded. mode="json" gives the status exactly
        # as it appears in the payment.done event.
        PAYMENTS_PROCESSED.labels(status=payment.model_dump(mode="json")["status"]).inc()
        log.info(
            "payment processed",
            extra={
                "order_id": str(payment.order_id),
                "payment_id": str(payment.payment_id),
                "status": payment.status,
                "amount_cents": payment.amount_cents,
            },
        )
        return payment

    async def run(self) -> None:
        s = self.settings
        consumer = AIOKafkaConsumer(
            s.input_topic,
            bootstrap_servers=s.kafka_bootstrap_servers,
            group_id=s.consumer_group,
            enable_auto_commit=False,  # we commit only after publishing the result
            auto_offset_reset="earliest",
        )
        producer = AIOKafkaProducer(
            bootstrap_servers=s.kafka_bootstrap_servers,
            enable_idempotence=True,  # no duplicate writes on producer retries
        )
        await producer.start()
        try:
            await consumer.start()
            try:
                self.ready = True
                log.info("connected", extra={"topic": s.input_topic, "group": s.consumer_group})
                async for msg in consumer:
                    await self.handle(msg.value, producer, msg.headers)
                    # Commit after publishing: if we crash before this line, the
                    # message is processed again (at-least-once delivery).
                    await consumer.commit()
            finally:
                self.ready = False
                await consumer.stop()
        finally:
            await producer.stop()
