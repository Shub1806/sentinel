package io.github.shub1806.sentinel.orders;

import java.util.List;
import java.util.Optional;
import java.util.UUID;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

public interface OutboxEventRepository extends JpaRepository<OutboxEvent, UUID> {

    List<OutboxEvent> findByAggregateId(UUID aggregateId);

    /** How many events are not sent yet. */
    long countByPublishedAtIsNull();

    /** The oldest event that is not sent yet, if any. */
    Optional<OutboxEvent> findFirstByPublishedAtIsNullOrderByCreatedAtAsc();

    /**
     * Locks the oldest unsent events. SKIP LOCKED means a second copy of the service
     * skips rows the first copy is already sending, instead of waiting or sending them twice.
     */
    @Query(value = """
            SELECT * FROM outbox_events
            WHERE published_at IS NULL
            ORDER BY created_at
            LIMIT :limit
            FOR UPDATE SKIP LOCKED
            """, nativeQuery = true)
    List<OutboxEvent> lockNextBatch(@Param("limit") int limit);
}
