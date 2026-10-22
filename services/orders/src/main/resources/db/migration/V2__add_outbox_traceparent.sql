-- V2: remember which trace created each outbox event. The relay publishes the event later, on
-- another thread, when the HTTP request's trace is long gone; with this it can continue that trace.
-- W3C traceparent: 00-<32 hex trace id>-<16 hex span id>-<2 hex flags> = 55 characters.
-- NULL for events created before this column existed, or when nothing was being traced.
ALTER TABLE outbox_events ADD COLUMN traceparent VARCHAR(55);
