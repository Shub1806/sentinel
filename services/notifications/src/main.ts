import { trace } from '@opentelemetry/api'
import { collectDefaultMetrics } from 'prom-client'
import { Consumer, MessagesStreamFallbackModes, MessagesStreamModes, stringDeserializers } from '@platformatic/kafka'
import { type Config, loadConfig } from './config.ts'
import { handleMessage } from './handler.ts'
import { startHealthServer, type HealthState } from './health.ts'
import { log } from './logger.ts'
import { S3ReceiptStore } from './receipt-store.ts'
import { createTracerProvider, traceMessage, tracedStore } from './tracing.ts'

let config: Config
try {
  config = loadConfig()
} catch (error) {
  // A bad setting stops startup with one clear line instead of a stack trace.
  log.error('cannot start', { error: error instanceof Error ? error.message : String(error) })
  process.exit(1)
}

// Node.js runtime metrics for /metrics: memory, CPU, event-loop delay, garbage collection.
collectDefaultMetrics()
const store = new S3ReceiptStore(config)
const tracerProvider = createTracerProvider(config.otlpTracesEndpoint, config.serviceName)
const tracer = tracerProvider?.getTracer('notifications') ?? trace.getTracer('notifications')
const state: HealthState = { alive: true, ready: false }
const server = startHealthServer(state, config.port)

const consumer = new Consumer({
  clientId: 'notifications',
  groupId: config.consumerGroup,
  bootstrapBrokers: config.kafkaBrokers,
  deserializers: stringDeserializers,
})

async function run(): Promise<void> {
  const stream = await consumer.consume({
    topics: [config.inputTopic],
    autocommit: false, // we commit only after the receipt is saved
    mode: MessagesStreamModes.COMMITTED, // continue where this group left off...
    fallbackMode: MessagesStreamFallbackModes.EARLIEST, // ...or from the start the first time
  })
  state.ready = true
  log.info('connected', { topic: config.inputTopic, group: config.consumerGroup, port: config.port })

  for await (const message of stream) {
    await traceMessage(tracer, config.inputTopic, message.headers, (ctx) =>
      handleMessage(message.value, tracedStore(store, tracer, ctx)),
    )
    // Commit after the work is done: if we crash before this line,
    // the message is processed again (at-least-once delivery).
    await message.commit()
  }
}

let shuttingDown = false
async function shutdown(signal: string): Promise<void> {
  if (shuttingDown) return
  shuttingDown = true
  log.info('shutting down', { signal })
  state.ready = false
  await consumer.close(true)
  await tracerProvider?.shutdown() // send the spans still waiting in the batch
  server.close()
}
process.on('SIGTERM', () => void shutdown('SIGTERM'))
process.on('SIGINT', () => void shutdown('SIGINT'))

run().catch((error: unknown) => {
  if (shuttingDown) return
  // Keep the process (and /healthz) up so the failure is visible; Kubernetes restarts the pod.
  state.alive = false
  state.ready = false
  log.error('worker stopped with an error', { error: String(error) })
})
