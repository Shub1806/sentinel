package io.github.shub1806.sentinel.orders;

import com.fasterxml.jackson.annotation.JsonProperty;
import java.time.Instant;
import java.util.UUID;

public record OrderResponse(
        @JsonProperty("id") UUID id,
        @JsonProperty("customer_id") String customerId,
        @JsonProperty("amount_cents") long amountCents,
        @JsonProperty("currency") String currency,
        @JsonProperty("created_at") Instant createdAt) {

    static OrderResponse from(CustomerOrder order) {
        return new OrderResponse(
                order.getId(),
                order.getCustomerId(),
                order.getAmountCents(),
                order.getCurrency(),
                order.getCreatedAt());
    }
}
