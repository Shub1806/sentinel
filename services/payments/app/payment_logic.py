"""The "charge" itself. Pure function: no Kafka, no network, easy to test."""

import uuid
from datetime import datetime

from app.models import OrderCreated, PaymentDone

# Fixed namespace so IDs derived from an order are always the same.
_NAMESPACE = uuid.UUID("6f1c2a52-6b0e-4a39-9d8e-3c2f6f0b5e11")


def charge(order: OrderCreated, *, max_amount_cents: int, now: datetime) -> PaymentDone:
    """Simulate charging an order.

    IDs are derived from order_id, so processing the same order twice
    produces the same payment_id and event_id. Downstream services can
    use that to drop duplicates (Kafka delivers at least once).
    """
    payment_id = uuid.uuid5(_NAMESPACE, f"payment:{order.order_id}")
    event_id = uuid.uuid5(_NAMESPACE, f"payment.done:{order.order_id}")

    if order.amount_cents > max_amount_cents:
        status, reason = "failed", "amount_exceeds_limit"
    else:
        status, reason = "succeeded", None

    return PaymentDone(
        event_id=event_id,
        occurred_at=now,
        payment_id=payment_id,
        order_id=order.order_id,
        amount_cents=order.amount_cents,
        currency=order.currency,
        status=status,
        failure_reason=reason,
    )
