import { describe, expect, it } from 'vitest'
import { keepConnected, retryUntilDone } from '../src/retry.ts'

/** A sleep that doesn't wait, but remembers how long it was asked to wait. */
function recordingSleep() {
  const waits: number[] = []
  return { waits, sleep: async (ms: number) => void waits.push(ms) }
}

/** An attempt that fails `times` times, then returns 'done'. */
function failingTimes(times: number) {
  let calls = 0
  return {
    get calls() {
      return calls
    },
    attempt: async () => {
      calls += 1
      if (calls <= times) throw new Error(`S3 down (${calls})`)
      return 'done'
    },
  }
}

describe('retryUntilDone', () => {
  it('returns at once when the first try works', async () => {
    const { waits, sleep } = recordingSleep()
    const work = failingTimes(0)

    await expect(retryUntilDone(work.attempt, { initialDelayMs: 1000, maxDelayMs: 30_000, sleep })).resolves.toBe('done')
    expect(work.calls).toBe(1)
    expect(waits).toEqual([])
  })

  it('keeps trying, doubling the pause up to the maximum', async () => {
    const { waits, sleep } = recordingSleep()
    const work = failingTimes(6)
    const failures: number[] = []

    const result = await retryUntilDone(work.attempt, { initialDelayMs: 1000, maxDelayMs: 5000, sleep }, (_e, n) =>
      failures.push(n),
    )

    expect(result).toBe('done')
    expect(work.calls).toBe(7)
    expect(waits).toEqual([1000, 2000, 4000, 5000, 5000, 5000])
    expect(failures).toEqual([1, 2, 3, 4, 5, 6])
  })

  it('reports each failure with the error and the next pause', async () => {
    const { sleep } = recordingSleep()
    const seen: string[] = []

    await retryUntilDone(failingTimes(1).attempt, { initialDelayMs: 250, maxDelayMs: 1000, sleep }, (error, _n, next) =>
      seen.push(`${String(error)} / ${next}`),
    )

    expect(seen).toEqual(['Error: S3 down (1) / 250'])
  })

  it('gives up with the real error when stopped while waiting', async () => {
    const stop = new AbortController()
    const work = failingTimes(100)
    const sleep = async () => {
      stop.abort()
      throw new Error('aborted')
    }

    await expect(
      retryUntilDone(work.attempt, { initialDelayMs: 1000, maxDelayMs: 5000, signal: stop.signal, sleep }),
    ).rejects.toThrow('S3 down (1)')
    expect(work.calls).toBe(1)
  })

  it('does not start waiting once already stopped', async () => {
    const stop = new AbortController()
    stop.abort()
    const { waits, sleep } = recordingSleep()

    await expect(
      retryUntilDone(failingTimes(1).attempt, { initialDelayMs: 1000, maxDelayMs: 5000, signal: stop.signal, sleep }),
    ).rejects.toThrow('S3 down (1)')
    expect(waits).toEqual([])
  })

  it('really waits by default, and the wait can be interrupted', async () => {
    const stop = new AbortController()
    setTimeout(() => stop.abort(), 20)
    const started = Date.now()

    await expect(
      retryUntilDone(failingTimes(100).attempt, { initialDelayMs: 10_000, maxDelayMs: 10_000, signal: stop.signal }),
    ).rejects.toThrow('S3 down (1)')
    expect(Date.now() - started).toBeLessThan(1000)
  })
})

describe('keepConnected', () => {
  const options = { initialDelayMs: 1000, maxDelayMs: 30_000 }

  it('keeps trying to connect, doubling the pause up to the maximum', async () => {
    const { waits, sleep } = recordingSleep()
    const lost: number[] = []
    let calls = 0

    await keepConnected(
      async () => {
        calls += 1
        if (calls <= 7) throw new Error('broker down')
      },
      { ...options, sleep },
      (_e, failures) => lost.push(failures),
    )

    expect(calls).toBe(8)
    expect(waits).toEqual([1000, 2000, 4000, 8000, 16_000, 30_000, 30_000])
    expect(lost).toEqual([1, 2, 3, 4, 5, 6, 7])
  })

  it('starts again from the shortest pause after a successful connect', async () => {
    const { waits, sleep } = recordingSleep()
    const lost: number[] = []
    let calls = 0

    await keepConnected(
      async (connected) => {
        calls += 1
        if (calls === 4) connected() // connected, then lost again
        if (calls <= 5) throw new Error('connection lost')
      },
      { ...options, sleep },
      (_e, failures) => lost.push(failures),
    )

    expect(waits).toEqual([1000, 2000, 4000, 1000, 2000])
    expect(lost).toEqual([1, 2, 3, 1, 2])
  })

  it('stops when aborted, and reports the real error', async () => {
    const stop = new AbortController()
    const sleep = async () => {
      stop.abort()
      throw new Error('aborted')
    }

    await expect(
      keepConnected(
        async () => {
          throw new Error('broker down')
        },
        { ...options, sleep, signal: stop.signal },
      ),
    ).rejects.toThrow('broker down')
  })

  it('does not retry an error that happens after shutdown started', async () => {
    const stop = new AbortController()
    const { waits, sleep } = recordingSleep()
    let calls = 0

    await expect(
      keepConnected(
        async () => {
          calls += 1
          stop.abort()
          throw new Error('closed during shutdown')
        },
        { ...options, sleep, signal: stop.signal },
      ),
    ).rejects.toThrow('closed during shutdown')
    expect(calls).toBe(1)
    expect(waits).toEqual([])
  })
})
