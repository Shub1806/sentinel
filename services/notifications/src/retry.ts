import { setTimeout as sleepFor } from 'node:timers/promises'

export interface RetryOptions {
  /** Wait after the first failure. Doubles after each further failure. */
  initialDelayMs: number
  /** The longest wait between two tries. */
  maxDelayMs: number
  /** Aborting stops the waiting (for example on shutdown); the last error is then thrown. */
  signal?: AbortSignal
  /** Replaceable in tests. */
  sleep?: (ms: number, signal?: AbortSignal) => Promise<unknown>
}

/**
 * Runs `attempt` until it succeeds, waiting longer after each failure. Used for one Kafka
 * message: the service stays up (and visible to monitoring) while a dependency is down,
 * instead of crashing and waiting out Kubernetes' restart back-off.
 */
export async function retryUntilDone<T>(
  attempt: () => Promise<T>,
  options: RetryOptions,
  onFailure: (error: unknown, failures: number, nextDelayMs: number) => void = () => {},
): Promise<T> {
  const sleep = options.sleep ?? ((ms: number, signal?: AbortSignal) => sleepFor(ms, undefined, { signal }))
  let delay = options.initialDelayMs
  let failures = 0
  for (;;) {
    try {
      return await attempt()
    } catch (error) {
      failures += 1
      if (options.signal?.aborted) throw error
      onFailure(error, failures, delay)
      try {
        await sleep(delay, options.signal)
      } catch {
        throw error // aborted while waiting: report the real failure, not the abort
      }
      delay = Math.min(delay * 2, options.maxDelayMs)
    }
  }
}

/**
 * Keeps a long-running connection going: runs `connectAndRun`, and if it fails, waits and
 * runs it again, longer after each failure. `connectAndRun` calls `connected()` once it is
 * connected, so the next outage starts again from the shortest wait. Returns when
 * `connectAndRun` returns; throws when `signal` is aborted (for example on shutdown).
 */
export async function keepConnected(
  connectAndRun: (connected: () => void) => Promise<void>,
  options: RetryOptions,
  onLost: (error: unknown, failures: number, nextDelayMs: number) => void = () => {},
): Promise<void> {
  const sleep = options.sleep ?? ((ms: number, signal?: AbortSignal) => sleepFor(ms, undefined, { signal }))
  let delay = options.initialDelayMs
  let failures = 0
  const connected = () => {
    delay = options.initialDelayMs
    failures = 0
  }
  for (;;) {
    try {
      return await connectAndRun(connected)
    } catch (error) {
      if (options.signal?.aborted) throw error
      failures += 1
      onLost(error, failures, delay)
      try {
        await sleep(delay, options.signal)
      } catch {
        throw error // aborted while waiting: report the real failure, not the abort
      }
      delay = Math.min(delay * 2, options.maxDelayMs)
    }
  }
}
