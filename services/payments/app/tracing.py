"""Tracing setup. Traces go to an OTLP endpoint (Tempo) only when one is configured."""

import os
from collections.abc import Mapping

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

ENDPOINT_VARIABLE = "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"


def build_provider(endpoint: str, service_name: str = "payments") -> TracerProvider:
    """A provider that batches spans and sends them over OTLP/HTTP."""
    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
    return provider


def configure_tracing(env: Mapping[str, str] = os.environ) -> TracerProvider | None:
    """Turns tracing on when the endpoint is set (in Kubernetes). Locally and in tests it
    stays off: spans are then no-ops and nothing is sent anywhere."""
    endpoint = env.get(ENDPOINT_VARIABLE, "").strip()
    if not endpoint:
        return None
    provider = build_provider(endpoint, env.get("OTEL_SERVICE_NAME", "payments"))
    trace.set_tracer_provider(provider)
    return provider
