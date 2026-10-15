"""Event models. These mirror the JSON Schemas in /contracts."""

from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class OrderCreated(BaseModel):
    # Ignore unknown fields so the orders service can add optional fields later.
    model_config = ConfigDict(extra="ignore", frozen=True)

    event_id: UUID
    event_type: Literal["order.created"]
    occurred_at: AwareDatetime
    order_id: UUID
    customer_id: str = Field(min_length=1, max_length=64)
    amount_cents: int = Field(ge=1)
    currency: str = Field(pattern=r"^[A-Z]{3}$")


class PaymentDone(BaseModel):
    model_config = ConfigDict(frozen=True)

    event_id: UUID
    event_type: Literal["payment.done"] = "payment.done"
    occurred_at: AwareDatetime
    payment_id: UUID
    order_id: UUID
    amount_cents: int = Field(ge=1)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    status: Literal["succeeded", "failed"]
    failure_reason: str | None = None
