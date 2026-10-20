import { describe, expect, it } from 'vitest'
import { ConfigError, loadConfig } from '../src/config.ts'

describe('loadConfig', () => {
  it('uses sensible defaults', () => {
    const c = loadConfig({ KAFKA_BOOTSTRAP_SERVERS: 'localhost:19092' })
    expect(c.kafkaBrokers).toEqual(['localhost:19092'])
    expect(c.inputTopic).toBe('payment.done')
    expect(c.receiptsBucket).toBe('sentinel-receipts')
    expect(c.awsEndpointUrl).toBeUndefined()
    expect(c.port).toBe(8002)
  })

  it('splits a comma-separated broker list', () => {
    const c = loadConfig({ KAFKA_BOOTSTRAP_SERVERS: 'a:9092, b:9092' })
    expect(c.kafkaBrokers).toEqual(['a:9092', 'b:9092'])
  })

  it('fails clearly when the broker address is missing', () => {
    expect(() => loadConfig({})).toThrow(ConfigError)
    expect(() => loadConfig({})).toThrow(/KAFKA_BOOTSTRAP_SERVERS: is required/)
    // An empty value (KEY= in .env) counts as missing too.
    expect(() => loadConfig({ KAFKA_BOOTSTRAP_SERVERS: '' })).toThrow(/KAFKA_BOOTSTRAP_SERVERS: is required/)
  })

  it('rejects a bad port and a bad endpoint URL', () => {
    expect(() => loadConfig({ KAFKA_BOOTSTRAP_SERVERS: 'x:1', PORT: 'abc' })).toThrow(/PORT/)
    expect(() => loadConfig({ KAFKA_BOOTSTRAP_SERVERS: 'x:1', AWS_ENDPOINT_URL: 'not a url' })).toThrow(
      /AWS_ENDPOINT_URL/,
    )
  })
})
