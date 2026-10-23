import { trace } from '@opentelemetry/api'
import { collectDefaultMetrics } from 'prom-client'
import { Consumer, MessagesStreamFallbackModes, MessagesStreamModes, stringDeserializers } from '@platformatic/kafka'
import { type Config, loadConfig } from './config.ts'
import { handleMessage } from './handler.ts'
import { startHealthServer, type HealthState } from './health.ts'
import { log } from './logger.ts'
import { S3ReceiptStore } from './receipt-store.ts'
import { keepConnected, retryUntilDone } from './retry.ts'
import { createTracerProvider, enableContext, traceMessage, tracedStore } from './tracing.ts'

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
enableContext()
const tracerProvider = createTracerProvider(config.otlpTracesEndpoint, config.serviceName)
const tracer = tracerProvider?.getTracer('notifications') ?? trace.getTracer('notifications')
const state: HealthState = { alive: true, ready: false }
const server = startHealthServer(state, config.port)

// Aborted on shutdown, so a message that is waiting to be retried doesn't hold the pod up.
const stopping = new AbortController()

function newConsumer() {
  return new Consumer({
    clientId: 'notifications',
    groupId: config.consumerGroup,
    bootstrapBrokers: config.kafkaBrokers,
    deserializers: stringDeserializers,
  })
}

// A fresh consumer for each connection attempt; the current one is closed on shutdown.
let consumer: ReturnType<typeof newConsumer> | undefined

async function consumeOnce(connected: () => void): Promise<void> {
  consumer = newConsumer()
  const stream = await consumer.consume({
    topics: [config.inputTopic],
    autocommit: false, // we commit only after the receipt is saved
    mode: MessagesStreamModes.COMMITTED, // continue where this group left off...
    fallbackMode: MessagesStreamFallbackModes.EARLIEST, // ...or from the start the first time
  })
  connected()
  state.ready = true
  log.info('connected', { topic: config.inputTopic, group: config.consumerGroup, port: config.port })

  for await (const message of stream) {
    // If saving fails (for example S3 is down), retry the same message with growing pauses.
    // The pod stays up, so the error count and lag stay visible to monitoring.
    await retryUntilDone(
      () =>
        traceMessage(tracer, config.inputTopic, message.headers, (ctx) =>
          handleMessage(message.value, tracedStore(store, tracer, ctx)),
        ),
      { initialDelayMs: 1000, maxDelayMs: 30_000, signal: stopping.signal },
      (error, failures, nextDelayMs) =>
        log.warn('receipt not saved, will retry', { error: String(error), failures, retry_in_ms: nextDelayMs }),
    )
    // Commit after the work is done: if we crash before this line,
    // the message is processed again (at-least-once delivery).
    await message.commit()
  }
  if (!stopping.signal.aborted) throw new Error('message stream ended unexpectedly')
}

async function run(): Promise<void> {
  // If Redpanda is down or the connection is lost, connect again with growing pauses.
  // The pod stays alive but not ready, instead of being restarted in a loop.
  await keepConnected(
    consumeOnce,
    { initialDelayMs: 1000, maxDelayMs: 30_000, signal: stopping.signal },
    (error, failures, nextDelayMs) => {
      state.ready = false
      log.warn('lost connection to Redpanda, reconnecting', { error: String(error), failures, retry_in_ms: nextDelayMs })
      consumer?.close(true).catch(() => {}) // best effort: the broker may be unreachable
    },
  )
}

let shuttingDown = false
async function shutdown(signal: string): Promise<void> {
  if (shuttingDown) return
  shuttingDown = true
  log.info('shutting down', { signal })
  state.ready = false
  stopping.abort()
  await consumer?.close(true)
  await tracerProvider?.shutdown() // send the spans still waiting in the batch
  server.close()
}
process.on('SIGTERM', () => void shutdown('SIGTERM'))
process.on('SIGINT', () => void shutdown('SIGINT'))

run().catch((error: unknown) => {
  if (shuttingDown) return
  // Only unexpected errors get here (connection problems are retried above).
  // Keep the process (and /healthz) up so the failure is visible; Kubernetes restarts the pod.
  state.alive = false
  state.ready = false
  log.error('worker stopped with an error', { error: String(error) })
})
