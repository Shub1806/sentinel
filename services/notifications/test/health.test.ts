import type { AddressInfo } from 'node:net'
import { afterEach, describe, expect, it } from 'vitest'
import { type HealthState, startHealthServer } from '../src/health.ts'

const servers: Array<{ close: () => void }> = []
afterEach(() => {
  for (const s of servers.splice(0)) s.close()
})

async function statusOf(state: HealthState, path: string): Promise<number> {
  const server = startHealthServer(state, 0) // port 0 = pick any free port
  servers.push(server)
  await new Promise((resolve) => server.once('listening', resolve))
  const { port } = server.address() as AddressInfo
  const response = await fetch(`http://127.0.0.1:${port}${path}`)
  return response.status
}

describe('health endpoints', () => {
  it('is alive and ready when connected', async () => {
    expect(await statusOf({ alive: true, ready: true }, '/healthz')).toBe(200)
    expect(await statusOf({ alive: true, ready: true }, '/readyz')).toBe(200)
  })

  it('is alive but not ready while connecting', async () => {
    expect(await statusOf({ alive: true, ready: false }, '/healthz')).toBe(200)
    expect(await statusOf({ alive: true, ready: false }, '/readyz')).toBe(503)
  })

  it('reports unhealthy after the worker stops', async () => {
    expect(await statusOf({ alive: false, ready: false }, '/healthz')).toBe(503)
  })
})
