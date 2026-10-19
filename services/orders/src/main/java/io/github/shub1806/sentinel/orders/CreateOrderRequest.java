package io.github.shub1806.sentinel.orders;

import com.fasterxml.jackson.annotation.JsonProperty;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Pattern;
import jakarta.validation.constraints.Positive;
import jakarta.validation.constraints.Size;

/** The JSON body of POST /orders. Field names are snake_case to match our contracts. */
public record CreateOrderRequest(
        @JsonProperty("customer_id") @NotBlank @Size(max = 64) String customerId,
        @JsonProperty("amount_cents") @NotNull @Positive Long amountCents,
        @JsonProperty("currency") @NotBlank @Pattern(regexp = "^[A-Z]{3}$") String currency) {
}
