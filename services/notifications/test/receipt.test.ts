import { describe, expect, it } from 'vitest'
import { formatAmount, receiptKey } from '../src/receipt.ts'

describe('formatAmount', () => {
  it.each([
    [1999, 'USD', 'USD 19.99'],
    [5, 'USD', 'USD 0.05'],
    [100000, 'EUR', 'EUR 1000.00'],
    [1, 'GBP', 'GBP 0.01'],
  ])('%i cents in %s -> %s', (cents, currency, expected) => {
    expect(formatAmount(cents, currency)).toBe(expected)
  })
})

describe('receiptKey', () => {
  it('depends only on the order id, so retries overwrite instead of duplicating', () => {
    expect(receiptKey('22222222-2222-2222-2222-222222222222')).toBe(
      'receipts/22222222-2222-2222-2222-222222222222.json',
    )
  })
})
