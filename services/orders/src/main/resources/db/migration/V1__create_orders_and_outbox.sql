-- V1: orders and the transactional outbox.
-- Flyway runs each file once, in version order, and records it in flyway_schema_history.

CREATE TABLE orders (
    id           UUID         PRIMARY KEY,
    customer_id  VARCHAR(64)  NOT NULL,
    amount_cents BIGINT       NOT NULL CHECK (amount_cents > 0),
    currency     VARCHAR(3)   NOT NULL CHECK (currency ~ '^[A-Z]{3}$'),
    created_at   TIMESTAMPTZ  NOT NULL
);

-- Events waiting to be sent to Redpanda. Written in the SAME transaction as the order,
-- so an order can never exist without its event, and vice versa.
CREATE TABLE outbox_events (
    id           UUID         PRIMARY KEY,  -- becomes event_id in the message
    aggregate_id UUID         NOT NULL,     -- the order id; used as the Kafka message key
    topic        VARCHAR(255) NOT NULL,
    payload      JSONB        NOT NULL,
    created_at   TIMESTAMPTZ  NOT NULL,
    published_at TIMESTAMPTZ               -- NULL until the relay has sent it
);

-- The relay asks "which events are not sent yet, oldest first?". This partial index
-- only holds unsent rows, so it stays tiny no matter how many orders exist.
CREATE INDEX outbox_events_unpublished_idx
    ON outbox_events (created_at)
    WHERE published_at IS NULL;
