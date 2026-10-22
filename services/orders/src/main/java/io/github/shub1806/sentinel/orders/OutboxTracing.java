package io.github.shub1806.sentinel.orders;

import io.micrometer.tracing.Span;
import io.micrometer.tracing.Tracer;
import io.micrometer.tracing.propagation.Propagator;
import java.util.HashMap;
import java.util.Map;
import org.springframework.beans.factory.ObjectProvider;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Component;

/**
 * Carries a trace across the outbox. The order is saved inside the HTTP request's trace, but the
 * relay sends the event later on its own thread. So we save the trace as a W3C "traceparent" string
 * with the event, and the relay continues that trace when it publishes.
 */
@Component
public class OutboxTracing {

    static final String TRACEPARENT = "traceparent";

    private final Tracer tracer;
    private final Propagator propagator;

    @Autowired
    public OutboxTracing(ObjectProvider<Tracer> tracer, ObjectProvider<Propagator> propagator) {
        // Without a tracing setup (some tests), fall back to no-op versions that do nothing.
        this(tracer.getIfAvailable(() -> Tracer.NOOP), propagator.getIfAvailable(() -> Propagator.NOOP));
    }

    OutboxTracing(Tracer tracer, Propagator propagator) {
        this.tracer = tracer;
        this.propagator = propagator;
    }

    /** The traceparent of the current span, or null when nothing is being traced. */
    public String currentTraceparent() {
        Span span = tracer.currentSpan();
        if (span == null || span.isNoop()) {
            return null;
        }
        Map<String, String> carrier = new HashMap<>();
        propagator.inject(span.context(), carrier, Map::put);
        return carrier.get(TRACEPARENT);
    }

    /**
     * Starts the span around sending one outbox event. It continues the saved trace, or starts a
     * new trace when there is none (events saved before tracing existed).
     */
    public Span startPublishSpan(String traceparent, String topic) {
        Span.Builder builder = traceparent == null
                ? tracer.spanBuilder().setNoParent()
                : propagator.extract(Map.of(TRACEPARENT, traceparent), Map::get);
        return builder.name("outbox publish " + topic).tag("messaging.destination.name", topic).start();
    }

    /** Makes the span current, so the Kafka producer's own span (and message header) belongs to it. */
    public Tracer.SpanInScope inScope(Span span) {
        return tracer.withSpan(span);
    }
}
