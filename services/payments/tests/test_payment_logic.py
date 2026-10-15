import uuid
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.models import OrderCreated
from app.payment_logic import charge

NOW = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)


def make_order(**overrides) -> OrderCreated:
    data = {
        "event_id": str(uuid.uuid4()),
        "event_type": "order.created",
        "occurred_at": "2026-10-15T00:00:00Z",
        "order_id": "22222222-2222-2222-2222-222222222222",
        "customer_id": "cust-1",
        "amount_cents": 1999,
        "currency": "USD",
    }
    data.update(overrides)
    return OrderCreated.model_validate(data)


def test_small_order_succeeds():
    p = charge(make_order(), max_amount_cents=100_000, now=NOW)
    assert p.status == "succeeded"
    assert p.failure_reason is None
    assert p.amount_cents == 1999
    assert p.currency == "USD"


def test_order_over_limit_fails():
    p = charge(make_order(amount_cents=100_001), max_amount_cents=100_000, now=NOW)
    assert p.status == "failed"
    assert p.failure_reason == "amount_exceeds_limit"


def test_order_exactly_at_limit_succeeds():
    p = charge(make_order(amount_cents=100_000), max_amount_cents=100_000, now=NOW)
    assert p.status == "succeeded"


def test_same_order_gives_same_ids():
    # Reprocessing a message must not create a second, different payment.
    first = charge(make_order(), max_amount_cents=100_000, now=NOW)
    again = charge(make_order(), max_amount_cents=100_000, now=NOW)
    assert first.payment_id == again.payment_id
    assert first.event_id == again.event_id


def test_different_orders_give_different_ids():
    a = charge(make_order(), max_amount_cents=100_000, now=NOW)
    b = charge(make_order(order_id=str(uuid.uuid4())), max_amount_cents=100_000, now=NOW)
    assert a.payment_id != b.payment_id


@pytest.mark.parametrize(
    "overrides",
    [
        {"amount_cents": 0},
        {"amount_cents": 19.99},
        {"currency": "usd"},
        {"event_type": "order.updated"},
        {"order_id": "not-a-uuid"},
        {"occurred_at": "2026-10-15T00:00:00"},  # no timezone
    ],
)
def test_invalid_orders_are_rejected(overrides):
    with pytest.raises(ValidationError):
        make_order(**overrides)


def test_unknown_fields_are_ignored():
    order = make_order(coupon="SAVE10")
    assert order.amount_cents == 1999
