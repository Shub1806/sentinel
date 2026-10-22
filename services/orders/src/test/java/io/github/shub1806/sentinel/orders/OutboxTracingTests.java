package io.github.shub1806.sentinel.orders;

import static org.assertj.core.api.Assertions.assertThat;

import io.micrometer.tracing.Span;
import io.micrometer.tracing.Tracer;
import io.micrometer.tracing.otel.bridge.OtelCurrentTraceContext;
import io.micrometer.tracing.otel.bridge.OtelPropagator;
import io.micrometer.tracing.otel.bridge.OtelTracer;
import io.micrometer.tracing.propagation.Propagator;
import io.opentelemetry.api.trace.propagation.W3CTraceContextPropagator;
import io.opentelemetry.context.propagation.ContextPropagators;
import io.opentelemetry.sdk.trace.SdkTracerProvider;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;

/** Runs against a real in-memory OpenTelemetry tracer, without Spring. */
class OutboxTracingTests {

    private final SdkTracerProvider provider = SdkTracerProvider.builder().build();
    private final io.opentelemetry.api.trace.Tracer otel = provider.get("test");
    private final OtelTracer tracer = new OtelTracer(otel, new OtelCurrentTraceContext(), event -> {});
    private final OutboxTracing tracing = new OutboxTracing(
            tracer, new OtelPropagator(ContextPropagators.create(W3CTraceContextPropagator.getInstance()), otel));

    @AfterEach
    void closeProvider() {
        provider.close();
    }

    @Test
    void nothingTracedMeansNoTraceparent() {
        assertThat(tracing.currentTraceparent()).isNull();
    }

    @Test
    void currentSpanIsSavedAsW3cTraceparent() {
        Span request = tracer.nextSpan().name("POST /orders").start();
        try (Tracer.SpanInScope ignored = tracer.withSpan(request)) {
            String traceparent = tracing.currentTraceparent();
            // version-traceid-spanid-flags. The flags are a bit field: 01 = sampled; newer
            // OpenTelemetry also sets 02 ("random trace id", W3C Trace Context level 2).
            assertThat(traceparent)
                    .startsWith("00-" + request.context().traceId() + "-" + request.context().spanId() + "-")
                    .hasSize(55);
            int flags = Integer.parseInt(traceparent.substring(53), 16);
            assertThat(flags & 1).as("sampled flag").isEqualTo(1);
        } finally {
            request.end();
        }
    }

    @Test
    void publishSpanContinuesTheSavedTrace() {
        String saved = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01";

        Span publish = tracing.startPublishSpan(saved, "order.created");
        try {
            assertThat(publish.context().traceId()).isEqualTo("4bf92f3577b34da6a3ce929d0e0e4736");
            assertThat(publish.context().parentId()).isEqualTo("00f067aa0ba902b7");
        } finally {
            publish.end();
        }
    }

    @Test
    void publishSpanIsCurrentInsideItsScope() {
        Span publish = tracing.startPublishSpan("00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01", "t");
        try (Tracer.SpanInScope ignored = tracing.inScope(publish)) {
            // This is what the Kafka producer sees, so its span and header join the same trace.
            assertThat(tracing.currentTraceparent()).startsWith("00-4bf92f3577b34da6a3ce929d0e0e4736-");
        } finally {
            publish.end();
        }
    }

    @Test
    void withoutSavedTraceThePublishSpanStartsANewTrace() {
        Span publish = tracing.startPublishSpan(null, "order.created");
        try {
            assertThat(publish.context().traceId()).hasSize(32);
            assertThat(publish.context().parentId()).isNull();
        } finally {
            publish.end();
        }
    }

    @Test
    void noopTracingDoesNothingAndNeverFails() {
        OutboxTracing noop = new OutboxTracing(Tracer.NOOP, Propagator.NOOP);

        assertThat(noop.currentTraceparent()).isNull();
        Span publish = noop.startPublishSpan("00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01", "t");
        try (Tracer.SpanInScope ignored = noop.inScope(publish)) {
            assertThat(noop.currentTraceparent()).isNull();
        } finally {
            publish.end();
        }
    }
}
