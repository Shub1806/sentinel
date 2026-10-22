package io.github.shub1806.sentinel.orders;

import static org.assertj.core.api.Assertions.assertThat;

import java.time.Duration;
import java.util.List;
import java.util.Properties;
import org.apache.kafka.clients.consumer.Consumer;
import org.apache.kafka.clients.consumer.ConsumerConfig;
import org.apache.kafka.clients.consumer.ConsumerRecord;
import io.micrometer.core.instrument.MeterRegistry;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.context.annotation.Import;
import org.springframework.kafka.core.ConsumerFactory;

@Import(TestcontainersConfiguration.class)
@SpringBootTest(properties = "outbox.relay.enabled=false")
class OutboxRelayTests {

    @Autowired
    OrderService service;

    @Autowired
    OutboxRelay relay;

    @Autowired
    OutboxEventRepository outbox;

    @Autowired
    ConsumerFactory<?, ?> consumerFactory;

    @Autowired
    OutboxMetrics metrics;

    @Autowired
    MeterRegistry registry;

    @Test
    void relayPublishesPendingEventsAndMarksThemSent() {
        CustomerOrder order = service.create(new CreateOrderRequest("cust-relay", 2500L, "USD"));
        String orderId = order.getId().toString();

        int sent = relay.publishPending();

        assertThat(sent).isGreaterThanOrEqualTo(1);
        assertThat(outbox.findByAggregateId(order.getId()).get(0).getPublishedAt()).isNotNull();

        // Read the topic back and find our message (keyed by order id).
        Properties props = new Properties();
        props.put(ConsumerConfig.AUTO_OFFSET_RESET_CONFIG, "earliest");
        try (Consumer<?, ?> consumer = consumerFactory.createConsumer("relay-test", null, null, props)) {
            consumer.subscribe(List.of("order.created"));
            ConsumerRecord<?, ?> found = null;
            long deadline = System.currentTimeMillis() + 15_000;
            while (found == null && System.currentTimeMillis() < deadline) {
                for (ConsumerRecord<?, ?> record : consumer.poll(Duration.ofMillis(500))) {
                    if (orderId.equals(record.key())) {
                        found = record;
                    }
                }
            }
            assertThat(found).as("order.created message for %s", orderId).isNotNull();
            assertThat(found.value().toString()).contains(orderId).contains("order.created");
        }
    }

    @Test
    void nothingPendingMeansNothingSent() {
        relay.publishPending(); // send anything other tests left behind
        assertThat(relay.publishPending()).isZero();
    }

    @Test
    void metricsShowUnsentEventsUntilTheRelaySendsThem() {
        relay.publishPending(); // start from an empty outbox
        service.create(new CreateOrderRequest("cust-metrics", 1200L, "USD"));

        metrics.refresh();
        assertThat(registry.get("outbox.pending.events").gauge().value()).isEqualTo(1);
        assertThat(registry.get("outbox.oldest.pending.age").gauge().value()).isGreaterThanOrEqualTo(0);

        relay.publishPending();
        metrics.refresh();
        assertThat(registry.get("outbox.pending.events").gauge().value()).isZero();
        assertThat(registry.get("outbox.oldest.pending.age").gauge().value()).isZero();
    }
}
