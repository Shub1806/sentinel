import { z } from 'zod'

const schema = z.object({
  KAFKA_BOOTSTRAP_SERVERS: z.string({ error: 'is required (for example localhost:19092)' }),
  INPUT_TOPIC: z.string().default('payment.done'),
  CONSUMER_GROUP: z.string().default('notifications'),
  RECEIPTS_BUCKET: z.string().default('sentinel-receipts'),
  AWS_REGION: z.string().default('us-east-1'),
  // Set to http://localhost:4566 for LocalStack. Leave empty for real AWS.
  AWS_ENDPOINT_URL: z.url().optional(),
  PORT: z.coerce.number().int().min(1).max(65535).default(8002),
})

export interface Config {
  kafkaBrokers: string[]
  inputTopic: string
  consumerGroup: string
  receiptsBucket: string
  awsRegion: string
  awsEndpointUrl: string | undefined
  port: number
}

export class ConfigError extends Error {}

export function loadConfig(env: NodeJS.ProcessEnv = process.env): Config {
  // Treat empty strings like "not set", so KEY= in a .env file uses the default.
  const cleaned = Object.fromEntries(Object.entries(env).filter(([, value]) => value !== ''))
  const result = schema.safeParse(cleaned)
  if (!result.success) {
    const problems = result.error.issues.map((issue) => `${issue.path.join('.')}: ${issue.message}`)
    throw new ConfigError(`Invalid configuration: ${problems.join('; ')}`)
  }
  const c = result.data
  return {
    kafkaBrokers: c.KAFKA_BOOTSTRAP_SERVERS.split(',').map((s) => s.trim()),
    inputTopic: c.INPUT_TOPIC,
    consumerGroup: c.CONSUMER_GROUP,
    receiptsBucket: c.RECEIPTS_BUCKET,
    awsRegion: c.AWS_REGION,
    awsEndpointUrl: c.AWS_ENDPOINT_URL,
    port: c.PORT,
  }
}
