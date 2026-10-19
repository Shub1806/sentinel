package io.github.shub1806.sentinel.orders;

import java.time.Instant;
import java.time.temporal.ChronoUnit;
import java.util.List;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.support.TransactionTemplate;

/** Sends waiting outbox events to Redpanda, then marks them as published. */
@Component
public class OutboxRelay {

    private static final Logger log = LoggerFactory.getLogger(OutboxRelay.class);

    private final OutboxEventRepository outbox;
    private final KafkaTemplate<String, String> kafka;
    private final TransactionTemplate tx;
    private final boolean enabled;
    private final int batchSize;

    public OutboxRelay(
            OutboxEventRepository outbox,
            KafkaTemplate<String, String> kafka,
            PlatformTransactionManager transactionManager,
            @Value("${outbox.relay.enabled:true}") boolean enabled,
            @Value("${outbox.relay.batch-size:100}") int batchSize) {
        this.outbox = outbox;
        this.kafka = kafka;
        this.tx = new TransactionTemplate(transactionManager);
        this.enabled = enabled;
        this.batchSize = batchSize;
    }

    @Scheduled(fixedDelayString = "${outbox.relay.interval-ms:500}")
    public void runScheduled() {
        if (!enabled) {
            return;
        }
        try {
            publishPending();
        } catch (RuntimeException e) {
            // e.g. Redpanda is down. Nothing was marked as sent, so the next run retries.
            log.warn("outbox relay failed, will retry: {}", e.getMessage());
        }
    }

    /** Sends one batch. Returns how many events were published. */
    public int publishPending() {
        Integer published = tx.execute(status -> {
            List<OutboxEvent> batch = outbox.lockNextBatch(batchSize);
            for (OutboxEvent event : batch) {
                // .join() waits until Redpanda confirms it stored the message.
                kafka.send(event.getTopic(), event.getAggregateId().toString(), event.getPayload()).join();
                event.markPublished(Instant.now().truncatedTo(ChronoUnit.MICROS));
            }
            return batch.size();
        });
        int count = published == null ? 0 : published;
        if (count > 0) {
            log.info("outbox relay published {} event(s)", count);
        }
        return count;
    }
}
