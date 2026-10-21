import { createServer, type Server } from 'node:http'
import { metricsRegistry } from './metrics.ts'

export interface HealthState {
  /** false once the consumer loop has stopped: Kubernetes should restart the pod. */
  alive: boolean
  /** true once connected to Redpanda and receiving messages. */
  ready: boolean
}

/** Serves /healthz and /readyz for Kubernetes, and /metrics for Prometheus. */
export function startHealthServer(state: HealthState, port: number): Server {
  const server = createServer((req, res) => {
    const reply = (status: number, body: object) => {
      res.writeHead(status, { 'content-type': 'application/json' })
      res.end(JSON.stringify(body))
    }
    if (req.url === '/healthz') {
      return state.alive ? reply(200, { status: 'ok' }) : reply(503, { status: 'worker stopped' })
    }
    if (req.url === '/readyz') {
      return state.ready ? reply(200, { status: 'ready' }) : reply(503, { status: 'not ready' })
    }
    if (req.url === '/metrics') {
      metricsRegistry.metrics().then(
        (body) => {
          res.writeHead(200, { 'content-type': metricsRegistry.contentType })
          res.end(body)
        },
        () => reply(500, { status: 'metrics unavailable' }),
      )
      return
    }
    reply(404, { status: 'not found' })
  })
  server.listen(port)
  return server
}
