import { PaymentDone } from './events.ts'
import { log } from './logger.ts'
import { buildReceipt, receiptKey } from './receipt.ts'
import type { ReceiptStore } from './receipt-store.ts'

export type HandleResult = 'receipt_saved' | 'skipped_failed_payment' | 'skipped_invalid'

/**
 * Processes one payment.done message.
 * Invalid messages are skipped (logged), never crash the service.
 * If saving to S3 fails, the error is thrown so the message is NOT committed and gets retried.
 */
export async function handleMessage(
  raw: string | undefined,
  store: ReceiptStore,
  now: () => Date = () => new Date(),
): Promise<HandleResult> {
  let data: unknown
  try {
    data = JSON.parse(raw ?? '')
  } catch {
    log.warn('skipping invalid payment.done message', { reason: 'not JSON' })
    return 'skipped_invalid'
  }

  const parsed = PaymentDone.safeParse(data)
  if (!parsed.success) {
    log.warn('skipping invalid payment.done message', {
      errors: parsed.error.issues.map((i) => `${i.path.join('.')}: ${i.message}`),
    })
    return 'skipped_invalid'
  }

  const payment = parsed.data
  if (payment.status !== 'succeeded') {
    log.info('no receipt for failed payment', {
      order_id: payment.order_id,
      failure_reason: payment.failure_reason ?? null,
    })
    return 'skipped_failed_payment'
  }

  const key = receiptKey(payment.order_id)
  const receipt = buildReceipt(payment, now())
  await store.save(key, JSON.stringify(receipt, null, 2))
  log.info('receipt saved', { order_id: payment.order_id, key })
  return 'receipt_saved'
}
