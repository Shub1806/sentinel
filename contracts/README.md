# Event contracts

All events are JSON. The Kafka message key is always `order_id`, so every event
for the same order lands on the same partition and is processed in order.

| Topic           | Producer | Consumer      | Schema                                                   |
|-----------------|----------|---------------|----------------------------------------------------------|
| `order.created` | orders   | payments      | [order.created.schema.json](order.created.schema.json)   |
| `payment.done`  | payments | notifications | [payment.done.schema.json](payment.done.schema.json)     |

Each topic has 3 partitions, so at most 3 consumer instances can share the work.

Consumers ignore fields they don't recognize, so new optional fields can be added
without breaking anyone.