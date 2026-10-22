package io.github.shub1806.sentinel.orders;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

import io.micrometer.core.instrument.MeterRegistry;
import io.micrometer.core.instrument.simple.SimpleMeterRegistry;
import java.time.Clock;
import java.time.Instant;
import java.time.ZoneOffset;
import java.util.Optional;
import java.util.UUID;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.dao.DataAccessResourceFailureException;

/** Plain unit tests: no database or broker needed. */
class OutboxMetricsTests {

    private static final Instant NOW = Instant.parse("2026-10-22T12:00:00Z");

    private final OutboxEventRepository outbox = mock(OutboxEventRepository.class);
    private final MeterRegistry registry = new SimpleMeterRegistry();
    private OutboxMetrics metrics;

    @BeforeEach
    void setUp() {
        metrics = new OutboxMetrics(outbox, registry, Clock.fixed(NOW, ZoneOffset.UTC));
    }

    private double pending() {
        return registry.get("outbox.pending.events").gauge().value();
    }

    private double oldestAge() {
        return registry.get("outbox.oldest.pending.age").gauge().value();
    }

    private static OutboxEvent eventCreatedAt(Instant createdAt) {
        return new OutboxEvent(UUID.randomUUID(), UUID.randomUUID(), "order.created", "{}", createdAt, null);
    }

    @Test
    void reportsNaNBeforeFirstRefresh() {
        assertThat(pending()).isNaN();
        assertThat(oldestAge()).isNaN();
    }

    @Test
    void emptyOutboxIsZero() {
        when(outbox.countByPublishedAtIsNull()).thenReturn(0L);
        when(outbox.findFirstByPublishedAtIsNullOrderByCreatedAtAsc()).thenReturn(Optional.empty());

        metrics.refresh();

        assertThat(pending()).isZero();
        assertThat(oldestAge()).isZero();
    }

    @Test
    void reportsCountAndAgeOfOldestEvent() {
        when(outbox.countByPublishedAtIsNull()).thenReturn(42L);
        when(outbox.findFirstByPublishedAtIsNullOrderByCreatedAtAsc())
                .thenReturn(Optional.of(eventCreatedAt(NOW.minusMillis(90_500))));

        metrics.refresh();

        assertThat(pending()).isEqualTo(42);
        assertThat(oldestAge()).isEqualTo(90.5);
    }

    @Test
    void eventFromTheFutureCountsAsZeroAge() {
        // Clocks on two machines can differ slightly; never report a negative age.
        when(outbox.countByPublishedAtIsNull()).thenReturn(1L);
        when(outbox.findFirstByPublishedAtIsNullOrderByCreatedAtAsc())
                .thenReturn(Optional.of(eventCreatedAt(NOW.plusSeconds(2))));

        metrics.refresh();

        assertThat(oldestAge()).isZero();
    }

    @Test
    void databaseErrorReportsNaNInsteadOfOldValues() {
        when(outbox.countByPublishedAtIsNull()).thenReturn(5L);
        when(outbox.findFirstByPublishedAtIsNullOrderByCreatedAtAsc())
                .thenReturn(Optional.of(eventCreatedAt(NOW.minusSeconds(10))));
        metrics.refresh();
        assertThat(pending()).isEqualTo(5);

        when(outbox.countByPublishedAtIsNull()).thenThrow(new DataAccessResourceFailureException("db down"));
        metrics.refresh();

        assertThat(pending()).isNaN();
        assertThat(oldestAge()).isNaN();
    }
}
