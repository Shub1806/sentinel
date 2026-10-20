import { describe, expect, it, vi } from 'vitest'
import { handleMessage } from '../src/handler.ts'
import type { ReceiptStore } from '../src/receipt-store.ts'

vi.mock('../src/logger.ts', () => ({ log: { info: vi.fn(), warn: vi.fn(), error: vi.fn() } }))

class FakeStore implements ReceiptStore {
  saved = new Map<string, string>()
  async save(key: string, body: string): Promise<void> {
    this.saved.set(key, body)
  }
}

const ORDER_ID = '22222222-2222-2222-2222-222222222222'
const NOW = () => new Date('2026-10-19T12:00:00.000Z')

function payment(overrides: Record<string, unknown> = {}): string {
  return JSON.stringify({
    event_id: 'e06f9fc5-a634-530c-9ca6-62a516285cc5',
    event_type: 'payment.done',
    occurred_at: '2026-10-18T03:20:18.557937Z',
    payment_id: 'c8149600-78c9-535d-8902-7add4efa8224',
    order_id: ORDER_ID,
    amount_cents: 1999,
    currency: 'USD',
    status: 'succeeded',
    failure_reason: null,
    ...overrides,
  })
}

describe('handleMessage', () => {
  it('saves a receipt for a successful payment', async () => {
    const store = new FakeStore()
    const result = await handleMessage(payment(), store, NOW)

    expect(result).toBe('receipt_saved')
    const body = store.saved.get(`receipts/${ORDER_ID}.json`)
    expect(body).toBeDefined()
    const receipt = JSON.parse(body ?? '{}')
    expect(receipt).toMatchObject({
      order_id: ORDER_ID,
      amount_cents: 1999,
      amount_display: 'USD 19.99',
      issued_at: '2026-10-19T12:00:00.000Z',
    })
  })

  it('writes no receipt for a failed payment', async () => {
    const store = new FakeStore()
    const result = await handleMessage(
      payment({ status: 'failed', failure_reason: 'amount_exceeds_limit' }),
      store,
      NOW,
    )
    expect(result).toBe('skipped_failed_payment')
    expect(store.saved.size).toBe(0)
  })

  it('processing the same message twice keeps exactly one receipt', async () => {
    const store = new FakeStore()
    await handleMessage(payment(), store, NOW)
    await handleMessage(payment(), store, NOW)
    expect(store.saved.size).toBe(1)
  })

  it.each([
    ['not JSON', 'this is not json'],
    ['empty', undefined],
    ['wrong event type', payment({ event_type: 'order.created' })],
    ['float amount', payment({ amount_cents: 19.99 })],
    ['lowercase currency', payment({ currency: 'usd' })],
    ['missing order id', payment({ order_id: undefined })],
  ])('skips an invalid message (%s) without saving anything', async (_name, raw) => {
    const store = new FakeStore()
    expect(await handleMessage(raw, store, NOW)).toBe('skipped_invalid')
    expect(store.saved.size).toBe(0)
  })

  it('lets a storage failure bubble up, so the message is not committed', async () => {
    const failing: ReceiptStore = {
      save: async () => {
        throw new Error('S3 unavailable')
      },
    }
    await expect(handleMessage(payment(), failing, NOW)).rejects.toThrow('S3 unavailable')
  })
})
