package io.github.shub1806.sentinel.orders;

import io.micrometer.core.instrument.Gauge;
import io.micrometer.core.instrument.MeterRegistry;
import java.time.Clock;
import java.time.Duration;
import java.time.Instant;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

/**
 * Publishes how far behind the outbox is:
 * outbox_pending_events (rows not sent yet) and
 * outbox_oldest_pending_age_seconds (how long the oldest of them has waited, 0 when none).
 *
 * The values are refreshed on a timer, so a Prometheus scrape never runs a database query.
 * If the refresh fails (database down), both report NaN instead of an old number.
 */
@Component
public class OutboxMetrics {

    private static final Logger log = LoggerFactory.getLogger(OutboxMetrics.class);

    private final OutboxEventRepository outbox;
    private final Clock clock;
    private volatile double pending = Double.NaN;
    private volatile double oldestAgeSeconds = Double.NaN;

    @Autowired
    public OutboxMetrics(OutboxEventRepository outbox, MeterRegistry registry) {
        this(outbox, registry, Clock.systemUTC());
    }

    // For tests, with a fixed clock.
    OutboxMetrics(OutboxEventRepository outbox, MeterRegistry registry, Clock clock) {
        this.outbox = outbox;
        this.clock = clock;
        Gauge.builder("outbox.pending.events", this, m -> m.pending)
                .description("Outbox events not yet sent to Redpanda")
                .register(registry);
        Gauge.builder("outbox.oldest.pending.age", this, m -> m.oldestAgeSeconds)
                .description("How long the oldest unsent outbox event has waited")
                .baseUnit("seconds")
                .register(registry);
    }

    @Scheduled(fixedDelayString = "${outbox.metrics.interval-ms:10000}")
    public void refresh() {
        try {
            long count = outbox.countByPublishedAtIsNull();
            Instant now = clock.instant();
            double age = outbox.findFirstByPublishedAtIsNullOrderByCreatedAtAsc()
                    .map(e -> Math.max(0, Duration.between(e.getCreatedAt(), now).toMillis()) / 1000.0)
                    .orElse(0.0);
            pending = count;
            oldestAgeSeconds = age;
        } catch (RuntimeException e) {
            pending = Double.NaN;
            oldestAgeSeconds = Double.NaN;
            log.debug("outbox metrics not refreshed: {}", e.getMessage());
        }
    }
}
