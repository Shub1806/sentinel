import { Counter, Histogram, register } from 'prom-client'

/** Every possible outcome of handling one payment.done message. */
export const HANDLE_RESULTS = ['receipt_saved', 'skipped_failed_payment', 'skipped_invalid'] as const
export type HandleResult = (typeof HANDLE_RESULTS)[number]

export const messagesProcessed = new Counter({
  name: 'notifications_messages_processed_total',
  help: 'payment.done messages handled, by result.',
  labelNames: ['result'] as const,
})
// Start every result at 0, so each series exists (and alerts have data) before the first message.
for (const result of HANDLE_RESULTS) messagesProcessed.inc({ result }, 0)

export const messageErrors = new Counter({
  name: 'notifications_message_errors_total',
  help: 'Messages that failed (for example, S3 unreachable) and will be retried.',
})

export const processingSeconds = new Histogram({
  name: 'notifications_processing_seconds',
  help: 'Time to handle one payment.done message, including the S3 write.',
  buckets: [0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10],
})

/** The registry that GET /metrics serves. */
export { register as metricsRegistry }
