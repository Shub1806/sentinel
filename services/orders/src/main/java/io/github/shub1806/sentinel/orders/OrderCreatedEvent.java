package io.github.shub1806.sentinel.orders;

import com.fasterxml.jackson.annotation.JsonProperty;
import java.time.Instant;
import java.util.UUID;

/** Matches contracts/order.created.schema.json. */
public record OrderCreatedEvent(
        @JsonProperty("event_id") UUID eventId,
        @JsonProperty("event_type") String eventType,
        @JsonProperty("occurred_at") Instant occurredAt,
        @JsonProperty("order_id") UUID orderId,
        @JsonProperty("customer_id") String customerId,
        @JsonProperty("amount_cents") long amountCents,
        @JsonProperty("currency") String currency) {

    public static final String TYPE = "order.created";
}
