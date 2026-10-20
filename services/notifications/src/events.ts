import { z } from 'zod'

/**
 * Mirrors contracts/payment.done.schema.json. Unknown fields are ignored.
 * z.guid() accepts any 8-4-4-4-12 hex id (like JSON Schema "uuid" and the Python service);
 * z.uuid() would also demand a specific UUID version and reject some valid test ids.
 */
export const PaymentDone = z.object({
  event_id: z.guid(),
  event_type: z.literal('payment.done'),
  occurred_at: z.iso.datetime({ offset: true }),
  payment_id: z.guid(),
  order_id: z.guid(),
  amount_cents: z.number().int().min(1),
  currency: z.string().regex(/^[A-Z]{3}$/),
  status: z.enum(['succeeded', 'failed']),
  failure_reason: z.string().nullable().optional(),
})

export type PaymentDone = z.infer<typeof PaymentDone>
