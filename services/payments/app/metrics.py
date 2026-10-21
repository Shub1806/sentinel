"""Prometheus metrics for the payments service. Served at GET /metrics."""

from typing import get_args

from prometheus_client import Counter, Histogram, disable_created_metrics

from app.models import PaymentDone

# Skip the extra "<name>_created" timestamp series: they add noise and we don't use them.
disable_created_metrics()

PAYMENTS_PROCESSED = Counter(
    "payments_processed",  # exported as payments_processed_total
    "Payments processed, by result.",
    ["status"],
)

# Create every status series at 0 right away. Without this, "failed" doesn't exist until
# the first failure, and an alert on the failure rate would have no data to fire on.
# The values come from the PaymentDone model, so a new status is picked up automatically.
PAYMENT_STATUSES: tuple[str, ...] = get_args(PaymentDone.model_fields["status"].annotation)
for _status in PAYMENT_STATUSES:
    PAYMENTS_PROCESSED.labels(status=_status)

INVALID_MESSAGES = Counter(
    "payments_invalid_messages",  # exported as payments_invalid_messages_total
    "order.created messages skipped because they failed validation.",
)

PROCESSING_SECONDS = Histogram(
    "payments_processing_seconds",
    "Time to process one order.created message, including publishing payment.done.",
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
)
