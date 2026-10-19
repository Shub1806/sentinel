package io.github.shub1806.sentinel.orders;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import java.util.List;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.context.annotation.Import;
import org.springframework.dao.DataIntegrityViolationException;
import tools.jackson.databind.ObjectMapper;

@Import(TestcontainersConfiguration.class)
@SpringBootTest
class OrderServiceTests {

    @Autowired
    OrderService service;

    @Autowired
    CustomerOrderRepository orders;

    @Autowired
    OutboxEventRepository outbox;

    @Autowired
    ObjectMapper json;

    @Test
    void createSavesOrderAndOutboxEventTogether() {
        CustomerOrder order = service.create(new CreateOrderRequest("cust-1", 1999L, "USD"));

        assertThat(orders.findById(order.getId())).isPresent();

        List<OutboxEvent> events = outbox.findByAggregateId(order.getId());
        assertThat(events).hasSize(1);
        OutboxEvent saved = events.get(0);
        assertThat(saved.getTopic()).isEqualTo("order.created");
        assertThat(saved.getPublishedAt()).isNull();

        // The payload must follow contracts/order.created.schema.json (snake_case keys).
        assertThat(saved.getPayload()).contains("\"event_id\"", "\"order_id\"", "\"amount_cents\"");
        OrderCreatedEvent event = json.readValue(saved.getPayload(), OrderCreatedEvent.class);
        assertThat(event.eventId()).isEqualTo(saved.getId());
        assertThat(event.eventType()).isEqualTo("order.created");
        assertThat(event.orderId()).isEqualTo(order.getId());
        assertThat(event.amountCents()).isEqualTo(1999L);
        assertThat(event.currency()).isEqualTo("USD");
    }

    @Test
    void databaseRejectsInvalidOrderAndSavesNothing() {
        long ordersBefore = orders.count();
        long eventsBefore = outbox.count();

        // Skips the API's validation on purpose: the database CHECK rule must still refuse it,
        // and the transaction must roll back BOTH rows.
        assertThatThrownBy(() -> service.create(new CreateOrderRequest("cust-1", 0L, "USD")))
                .isInstanceOf(DataIntegrityViolationException.class);

        assertThat(orders.count()).isEqualTo(ordersBefore);
        assertThat(outbox.count()).isEqualTo(eventsBefore);
    }
}
