import { once } from 'node:events'
import type { AddressInfo } from 'node:net'
import { describe, expect, it } from 'vitest'
import { handleMessage } from '../src/handler.ts'
import { startHealthServer } from '../src/health.ts'
import { HANDLE_RESULTS, metricsRegistry } from '../src/metrics.ts'
import type { ReceiptStore } from '../src/receipt-store.ts'

function paymentDone(status: 'succeeded' | 'failed'): string {
  return JSON.stringify({
    event_id: '11111111-1111-1111-1111-111111111111',
    event_type: 'payment.done',
    occurred_at: '2026-10-15T00:00:00Z',
    payment_id: '33333333-3333-3333-3333-333333333333',
    order_id: '22222222-2222-2222-2222-222222222222',
    amount_cents: 1999,
    currency: 'USD',
    status,
    failure_reason: status === 'failed' ? 'amount over limit' : null,
  })
}

const okStore: ReceiptStore = { save: async () => {} }
const brokenStore: ReceiptStore = {
  save: async () => {
    throw new Error('S3 unreachable')
  },
}

/** Current value of one series, e.g. 'notifications_message_errors_total'. Missing = 0. */
async function sample(series: string): Promise<number> {
  const text = await metricsRegistry.metrics()
  const line = text.split('\n').find((l) => l.startsWith(`${series} `))
  return line ? Number(line.slice(series.length + 1)) : 0
}

const processed = (result: string) => `notifications_messages_processed_total{result="${result}"}`

// Metrics are shared by every test in this file, so each test compares before/after.
describe('handler metrics', () => {
  it('counts and times a saved receipt', async () => {
    const saved = await sample(processed('receipt_saved'))
    const timed = await sample('notifications_processing_seconds_count')

    expect(await handleMessage(paymentDone('succeeded'), okStore)).toBe('receipt_saved')

    expect(await sample(processed('receipt_saved'))).toBe(saved + 1)
    expect(await sample('notifications_processing_seconds_count')).toBe(timed + 1)
  })

  it('counts a failed payment as skipped, not as a receipt', async () => {
    const skipped = await sample(processed('skipped_failed_payment'))
    const saved = await sample(processed('receipt_saved'))

    expect(await handleMessage(paymentDone('failed'), okStore)).toBe('skipped_failed_payment')

    expect(await sample(processed('skipped_failed_payment'))).toBe(skipped + 1)
    expect(await sample(processed('receipt_saved'))).toBe(saved)
  })

  it('counts an invalid message', async () => {
    const invalid = await sample(processed('skipped_invalid'))

    expect(await handleMessage('not json', okStore)).toBe('skipped_invalid')

    expect(await sample(processed('skipped_invalid'))).toBe(invalid + 1)
  })

  it('counts an S3 failure as an error, not as a saved receipt', async () => {
    const errors = await sample('notifications_message_errors_total')
    const saved = await sample(processed('receipt_saved'))

    await expect(handleMessage(paymentDone('succeeded'), brokenStore)).rejects.toThrow('S3 unreachable')

    expect(await sample('notifications_message_errors_total')).toBe(errors + 1)
    expect(await sample(processed('receipt_saved'))).toBe(saved)
  })
})

describe('GET /metrics', () => {
  it('serves every result series in Prometheus format', async () => {
    const server = startHealthServer({ alive: true, ready: true }, 0) // 0 = any free port
    await once(server, 'listening')
    try {
      const { port } = server.address() as AddressInfo
      const response = await fetch(`http://127.0.0.1:${port}/metrics`)
      const body = await response.text()

      expect(response.status).toBe(200)
      expect(response.headers.get('content-type')).toContain('text/plain')
      // Every result must exist from the start (at 0), even before any message of that kind.
      for (const result of HANDLE_RESULTS) expect(body).toContain(processed(result))
      expect(body).toContain('notifications_message_errors_total')
    } finally {
      server.close()
    }
  })
})
