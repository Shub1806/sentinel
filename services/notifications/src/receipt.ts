import type { PaymentDone } from './events.ts'

export interface Receipt {
  receipt_id: string
  order_id: string
  payment_id: string
  amount_cents: number
  currency: string
  amount_display: string
  paid_at: string
  issued_at: string
}

/** 1999 + "USD" -> "USD 19.99". Whole-number maths only: no floating-point rounding. */
export function formatAmount(amountCents: number, currency: string): string {
  const whole = Math.floor(amountCents / 100)
  const cents = String(amountCents % 100).padStart(2, '0')
  return `${currency} ${whole}.${cents}`
}

/**
 * The S3 key depends only on the order id, so processing the same message twice
 * overwrites the same file instead of creating a duplicate receipt.
 */
export function receiptKey(orderId: string): string {
  return `receipts/${orderId}.json`
}

export function buildReceipt(payment: PaymentDone, now: Date): Receipt {
  return {
    receipt_id: payment.payment_id,
    order_id: payment.order_id,
    payment_id: payment.payment_id,
    amount_cents: payment.amount_cents,
    currency: payment.currency,
    amount_display: formatAmount(payment.amount_cents, payment.currency),
    paid_at: payment.occurred_at,
    issued_at: now.toISOString(),
  }
}
