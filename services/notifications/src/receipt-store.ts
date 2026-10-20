import { PutObjectCommand, S3Client } from '@aws-sdk/client-s3'
import type { Config } from './config.ts'

/** Where receipts are saved. An interface, so tests can use an in-memory fake. */
export interface ReceiptStore {
  save(key: string, body: string): Promise<void>
}

export class S3ReceiptStore implements ReceiptStore {
  private readonly client: S3Client
  private readonly bucket: string

  constructor(config: Config) {
    this.bucket = config.receiptsBucket
    this.client = new S3Client({
      region: config.awsRegion,
      endpoint: config.awsEndpointUrl,
      // LocalStack needs "path-style" URLs (http://localhost:4566/bucket/key).
      forcePathStyle: config.awsEndpointUrl !== undefined,
    })
  }

  async save(key: string, body: string): Promise<void> {
    await this.client.send(
      new PutObjectCommand({
        Bucket: this.bucket,
        Key: key,
        Body: body,
        ContentType: 'application/json',
      }),
    )
  }
}
