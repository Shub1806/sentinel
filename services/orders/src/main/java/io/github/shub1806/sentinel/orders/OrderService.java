package io.github.shub1806.sentinel.orders;

import java.time.Instant;
import java.time.temporal.ChronoUnit;
import java.util.UUID;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import tools.jackson.databind.ObjectMapper;

@Service
public class OrderService {

    static final String ORDER_CREATED_TOPIC = "order.created";
    private static final Logger log = LoggerFactory.getLogger(OrderService.class);

    private final CustomerOrderRepository orders;
    private final OutboxEventRepository outbox;
    private final ObjectMapper json;
    private final OutboxTracing tracing;

    public OrderService(
            CustomerOrderRepository orders, OutboxEventRepository outbox, ObjectMapper json, OutboxTracing tracing) {
        this.orders = orders;
        this.outbox = outbox;
        this.json = json;
        this.tracing = tracing;
    }

    /**
     * Saves the order AND its order.created event in ONE database transaction.
     * If anything fails, both are rolled back: we never get an order without its event.
     */
    @Transactional
    public CustomerOrder create(CreateOrderRequest request) {
        // Postgres stores microseconds, so trim to that: the DB and the message then match exactly.
        Instant now = Instant.now().truncatedTo(ChronoUnit.MICROS);

        CustomerOrder order = orders.save(new CustomerOrder(
                UUID.randomUUID(),
                request.customerId(),
                request.amountCents(),
                request.currency(),
                now));

        OrderCreatedEvent event = new OrderCreatedEvent(
                UUID.randomUUID(),
                OrderCreatedEvent.TYPE,
                now,
                order.getId(),
                order.getCustomerId(),
                order.getAmountCents(),
                order.getCurrency());

        outbox.save(new OutboxEvent(
                event.eventId(),
                order.getId(),
                ORDER_CREATED_TOPIC,
                json.writeValueAsString(event),
                now,
                tracing.currentTraceparent()));
        log.info("order created {} ({} {} cents)", order.getId(), order.getCurrency(), order.getAmountCents());
        return order;
    }
}
