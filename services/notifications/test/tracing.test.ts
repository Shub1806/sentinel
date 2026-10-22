import { SpanKind, SpanStatusCode, context } from '@opentelemetry/api'
import { InMemorySpanExporter, SimpleSpanProcessor, TracerProvider } from '@opentelemetry/sdk-trace'
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { handleMessage } from '../src/handler.ts'
import type { ReceiptStore } from '../src/receipt-store.ts'
import { createTracerProvider, enableContext, traceMessage, tracedStore } from '../src/tracing.ts'

// What payments puts on a payment.done message (trace id, then the id of its send span).
const TRACE_ID = '4bf92f3577b34da6a3ce929d0e0e4736'
const PAYMENTS_SPAN_ID = '00f067aa0ba902b7'
const HEADERS = new Map([['traceparent', `00-${TRACE_ID}-${PAYMENTS_SPAN_ID}-03`]])

const PAYMENT = JSON.stringify({
  event_id: '11111111-1111-4111-8111-111111111111',
  event_type: 'payment.done',
  occurred_at: '2026-10-15T00:00:00Z',
  payment_id: '33333333-3333-4333-8333-333333333333',
  order_id: '22222222-2222-4222-8222-222222222222',
  amount_cents: 1999,
  currency: 'USD',
  status: 'succeeded',
})

const memoryStore = (): ReceiptStore => ({ save: async () => {} })
const brokenStore: ReceiptStore = {
  save: async () => {
    throw new Error('S3 unreachable')
  },
}

const exporter = new InMemorySpanExporter()
const provider = new TracerProvider({ spanProcessors: [new SimpleSpanProcessor({ exporter })] })
const tracer = provider.getTracer('test')

function process(raw: string, headers: Map<string, string> | undefined, store: ReceiptStore) {
  return traceMessage(tracer, 'payment.done', headers, (ctx) => handleMessage(raw, tracedStore(store, tracer, ctx)))
}

const spanOf = (kind: SpanKind) => exporter.getFinishedSpans().filter((s) => s.kind === kind)

beforeAll(() => enableContext())
afterAll(() => context.disable())
afterEach(() => {
  exporter.reset()
  vi.restoreAllMocks()
})

describe('tracing', () => {
  it('continues the trace that payments started', async () => {
    await process(PAYMENT, HEADERS, memoryStore())

    const [consumer] = spanOf(SpanKind.CONSUMER)
    expect(consumer?.name).toBe('payment.done process')
    expect(consumer?.spanContext().traceId).toBe(TRACE_ID)
    expect(consumer?.parentSpanContext?.spanId).toBe(PAYMENTS_SPAN_ID)
    expect(consumer?.attributes['notifications.result']).toBe('receipt_saved')
  })

  it('shows the S3 write as a child span', async () => {
    await process(PAYMENT, HEADERS, memoryStore())

    const [consumer] = spanOf(SpanKind.CONSUMER)
    const [s3] = spanOf(SpanKind.CLIENT)
    expect(s3?.name).toBe('S3 PutObject')
    expect(s3?.spanContext().traceId).toBe(TRACE_ID)
    expect(s3?.parentSpanContext?.spanId).toBe(consumer?.spanContext().spanId)
    expect(s3?.attributes['aws.s3.key']).toBe('receipts/22222222-2222-4222-8222-222222222222.json')
  })

  it('starts a new trace when the message has no trace header', async () => {
    await process(PAYMENT, undefined, memoryStore())

    const [consumer] = spanOf(SpanKind.CONSUMER)
    expect(consumer?.parentSpanContext).toBeUndefined()
    expect(consumer?.spanContext().traceId).toMatch(/^[0-9a-f]{32}$/)
  })

  it('marks an invalid message as an error, without an S3 span', async () => {
    await process('not json', HEADERS, memoryStore())

    const [consumer] = spanOf(SpanKind.CONSUMER)
    expect(consumer?.status.code).toBe(SpanStatusCode.ERROR)
    expect(spanOf(SpanKind.CLIENT)).toHaveLength(0)
  })

  it('records an S3 failure on both spans and still throws, so the message is retried', async () => {
    await expect(process(PAYMENT, HEADERS, brokenStore)).rejects.toThrow('S3 unreachable')

    const [consumer] = spanOf(SpanKind.CONSUMER)
    const [s3] = spanOf(SpanKind.CLIENT)
    expect(s3?.status.code).toBe(SpanStatusCode.ERROR)
    expect(consumer?.status.code).toBe(SpanStatusCode.ERROR)
    expect(consumer?.events.some((e) => e.name === 'exception')).toBe(true)
  })

  it('puts the trace and span ids in log lines written while handling the message', async () => {
    const lines: string[] = []
    vi.spyOn(console, 'log').mockImplementation((line: string) => void lines.push(line))

    await process(PAYMENT, HEADERS, memoryStore())

    const [consumer] = spanOf(SpanKind.CONSUMER)
    const saved = lines.map((l) => JSON.parse(l)).find((e) => e.msg === 'receipt saved')
    expect(saved?.trace_id).toBe(TRACE_ID)
    expect(saved?.span_id).toBe(consumer?.spanContext().spanId)
  })

  it('leaves trace fields out of log lines written outside a message', async () => {
    const lines: string[] = []
    vi.spyOn(console, 'log').mockImplementation((line: string) => void lines.push(line))
    const { log } = await import('../src/logger.ts')

    log.info('connected')

    expect(JSON.parse(lines[0] ?? '{}')).not.toHaveProperty('trace_id')
  })

  it('is off without an endpoint', () => {
    expect(createTracerProvider(undefined, 'notifications')).toBeUndefined()
  })
})
