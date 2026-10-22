import {
  type Context,
  ROOT_CONTEXT,
  SpanKind,
  SpanStatusCode,
  type Tracer,
  context,
  defaultTextMapGetter,
  trace,
} from '@opentelemetry/api'
import { AsyncLocalStorageContextManager } from '@opentelemetry/context-async-hooks'
import { W3CTraceContextPropagator } from '@opentelemetry/core'
import { OTLPTraceExporter } from '@opentelemetry/exporter-trace-otlp-http'
import { resourceFromAttributes } from '@opentelemetry/resources'
import { BatchSpanProcessor, TracerProvider } from '@opentelemetry/sdk-trace'
import type { HandleResult } from './metrics.ts'
import type { ReceiptStore } from './receipt-store.ts'

const propagator = new W3CTraceContextPropagator()

/**
 * Lets code deeper down (like the logger) find the span of the message being handled.
 * Without it, @opentelemetry/api has no "current span" across awaits.
 */
export function enableContext(): void {
  context.setGlobalContextManager(new AsyncLocalStorageContextManager().enable())
}

/**
 * A provider that sends spans to Tempo over OTLP/HTTP, or undefined when no endpoint is set
 * (locally and in tests). Without a provider, the tracer from @opentelemetry/api does nothing.
 */
export function createTracerProvider(endpoint: string | undefined, serviceName: string): TracerProvider | undefined {
  if (endpoint === undefined) return undefined
  return new TracerProvider({
    resource: resourceFromAttributes({ 'service.name': serviceName }),
    spanProcessors: [new BatchSpanProcessor({ exporter: new OTLPTraceExporter({ url: endpoint }) })],
  })
}

/** Kafka headers as a plain object. Values that aren't strings are ignored. */
function toCarrier(headers: ReadonlyMap<unknown, unknown> | undefined): Record<string, string> {
  const carrier: Record<string, string> = {}
  for (const [key, value] of headers ?? []) {
    if (typeof key === 'string' && typeof value === 'string') carrier[key] = value
  }
  return carrier
}

/**
 * Runs the work for one message inside a consumer span that continues the trace from the
 * message headers (written by payments). The work gets the span's context for child spans.
 */
export async function traceMessage(
  tracer: Tracer,
  topic: string,
  headers: ReadonlyMap<unknown, unknown> | undefined,
  work: (ctx: Context) => Promise<HandleResult>,
): Promise<HandleResult> {
  const parent = propagator.extract(ROOT_CONTEXT, toCarrier(headers), defaultTextMapGetter)
  const span = tracer.startSpan(
    `${topic} process`,
    { kind: SpanKind.CONSUMER, attributes: { 'messaging.system': 'kafka', 'messaging.destination.name': topic } },
    parent,
  )
  try {
    const ctx = trace.setSpan(parent, span)
    const result = await context.with(ctx, () => work(ctx))
    span.setAttribute('notifications.result', result)
    if (result === 'skipped_invalid') {
      span.setStatus({ code: SpanStatusCode.ERROR, message: 'invalid payment.done message' })
    }
    return result
  } catch (error) {
    // The message is not committed and will be retried; the span shows why.
    span.recordException(error instanceof Error ? error : String(error))
    span.setStatus({ code: SpanStatusCode.ERROR, message: String(error) })
    throw error
  } finally {
    span.end()
  }
}

/** Wraps a receipt store so each save shows up as a child span of the message's span. */
export function tracedStore(store: ReceiptStore, tracer: Tracer, parent: Context): ReceiptStore {
  return {
    async save(key: string, body: string): Promise<void> {
      const span = tracer.startSpan(
        'S3 PutObject',
        { kind: SpanKind.CLIENT, attributes: { 'rpc.system': 'aws-api', 'rpc.service': 'S3', 'rpc.method': 'PutObject', 'aws.s3.key': key } },
        parent,
      )
      try {
        await store.save(key, body)
      } catch (error) {
        span.recordException(error instanceof Error ? error : String(error))
        span.setStatus({ code: SpanStatusCode.ERROR, message: String(error) })
        throw error
      } finally {
        span.end()
      }
    },
  }
}
