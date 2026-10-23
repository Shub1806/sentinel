# Chaos scenarios

Each script takes `inject` or `recover`. Start the order generator first, so the SLO alerts
have enough traffic: `kubectl apply -f infra/chaos/loadgen.yaml`.

| # | Scenario | Script |
|---|---|---|
| 1 | Bad deploy: payments crash-loops | `payments-bad-config.sh` |
| 2 | Database outage | `database-outage.sh` |
| 3 | S3 outage | `s3-outage.sh` |
| 4 | Slow consumer: payments falls behind | `slow-consumer.sh` |
| 5 | Poison messages: a producer sends bad events | `poison-messages.sh` |
| 6 | Broker outage: Redpanda is down | `broker-outage.sh` |

## 1. Bad deploy: payments crash-loops

**Inject:** `./scripts/chaos/payments-bad-config.sh inject` sets `MAX_AMOUNT_CENTS=abc` on the payments
Deployment. The new pod fails at startup; the rolling update keeps the old pod running.

**Expected**

| Signal | Evidence |
|---|---|
| Alerts | `PodRestarted` (about 1 min); `KubePodCrashLooping`, `KubePodNotReady` (after 15 min); `TargetDown` pending |
| Metrics | Payments throughput unchanged; consumer lag about 0 |
| Logs | `{app="payments"} \|= "MAX_AMOUNT_CENTS"`: ERROR from `uvicorn.error` with `ValueError ... 'abc'` in `config.py` |
| Traces | None from the new pod (it never processes a message) |

**Root cause:** an invalid setting in the new rollout. Customers are not affected; the release is stuck.

**Recover:** `./scripts/chaos/payments-bad-config.sh recover` removes the variable; Kubernetes scales the
old ReplicaSet back and the broken one away.

## 2. Database outage

**Inject:** `./scripts/chaos/database-outage.sh inject` hibernates the CloudNativePG cluster `orders-db`:
the Postgres pod is stopped (it takes about 3 minutes to go), the volume is kept.

**Expected**

| Signal | Evidence |
|---|---|
| Alerts | `KubePodNotReady`, `KubePdbNotEnoughHealthyPods` pending while Postgres shuts down; then `OrdersAvailabilityBudgetBurnFast` (critical) and `OrdersAvailabilityBudgetBurnSlow` pending. The latency alerts stay quiet: their 1-hour window is still mostly fast requests, and availability (0.1 % budget) is the stricter SLO |
| Metrics | `POST /orders` all `500 SERVER_ERROR` with `exception="CannotCreateTransactionException"`; p95 about 5.5 s (the connection pool waits `DB_CONNECTION_TIMEOUT_MS` = 5 s before giving up) |
| Logs | orders ERROR: `Servlet.service() ... CannotCreateTransactionException: Could not open JPA EntityManager for transaction`, with `traceId` |
| Traces | `http post /orders` spans of about 5 s, with an error status |

**Root cause:** the orders database is unavailable. payments and notifications are idle (no new events), not broken.

**Recover:** `./scripts/chaos/database-outage.sh recover` turns hibernation off and waits for the Postgres pod;
new orders succeed immediately, and the alerts clear as the 5-minute window fills with successes.

**Observed (first run):** the fast-burn alert took about 10 minutes from inject: about 3 minutes for Postgres to stop,
then the 5-minute window had to fill with failures. The order generator was closed-loop then (it waited for each
5-second failure), so traffic dropped from 2/s to 0.18/s and the error ratio rose slowly; it is open-loop now.
`OrdersAvailabilityBudgetBurnSlow` kept firing for about 30 minutes after recovery: its 30-minute window still held
the failures. That is intended (the incident used real error budget), but wait for it to clear before the next scenario.

## 3. S3 outage

**Inject:** `./scripts/chaos/s3-outage.sh inject` scales LocalStack to 0. LocalStack keeps S3 in memory, so existing
receipts are lost; the bucket is created again when it starts.

**What notifications does:** it retries the failed message in-process, pausing 1 s, 2 s, 4 s ... up to 30 s between
tries, and commits it only once the receipt is saved. The pod stays up and ready, so its error count stays visible.

**Expected (observed)**

| Signal | Evidence |
|---|---|
| Alerts | `NotificationErrors` (about 6 min: pending at once, firing after 5 min); `ConsumerLagHigh` for `notifications` once lag stays above 1000 for 5 min (about 13 min) |
| Metrics | `notifications_message_errors_total` climbs (about 2 per minute once the pause reaches 30 s); notifications lag grows by about 120 per minute (the order rate); no restarts; orders and payments unaffected |
| Logs | notifications WARN `receipt not saved, will retry` with `error: connect ECONNREFUSED <localstack ip>:4566`, `failures` and `retry_in_ms` |
| Traces | One `payment.done process` + `S3 PutObject` error span pair per retry |

**Root cause:** S3 is unreachable. Customers are not affected; receipts are delayed.

**Recover:** `./scripts/chaos/s3-outage.sh recover`. The next retry (at most 30 s later) succeeds and the backlog
drains on its own; no restart needed.

**History:** the first run (before the retry) showed a real weakness: one failed write stopped the consumer, the pod
restarted every 2 minutes and was never ready, Prometheus counted **0** errors, `NotificationErrors` never fired, the
cause was only in the logs, and recovery waited out up to 5 minutes of restart back-off. The in-process retry fixed all of it.

## 4. Slow consumer: payments falls behind

**Inject:** `./scripts/chaos/slow-consumer.sh inject 2000` sets `PROCESSING_DELAY_MS=2000` on payments. Each message
waits 2 s before it is handled, so one pod manages 0.5 per second while orders arrive at 2 per second.

**Expected (observed)**

| Signal | Evidence |
|---|---|
| Alerts | `ConsumerLagHigh` for `payments` (about 17 min: lag passes 1000 after about 11 min, then 5 more min); nothing else. Orders keep returning 201, so the orders SLO alerts stay quiet |
| Metrics | payments lag grows by about 90 per minute (2/s in, 0.5/s out); payments p95 about 2.4 s; payments throughput 0.5/s; notifications lag 0 (it only sees what payments sends) |
| Autoscaler | Does **not** help. CPU stays about 20 % of the 70 % target because waiting uses no CPU. It went to 3 pods once, for about a minute, on the rollout's startup CPU spike, then back to 1 |
| Consumer group | `rpk group describe payments`: one member owns all 3 partitions |
| Logs / traces | No errors. `order.created process` spans of about 2 s |

**Root cause:** payments handles messages more slowly than they arrive. Customers get their order accepted, but
payments and receipts fall further behind every minute.

**Recover:** `./scripts/chaos/slow-consumer.sh recover`. The new pod drained the backlog within about a minute of the
rollout; smoke test passed.

**Lesson:** CPU does not tell you whether a consumer keeps up. Scaling consumers on lag (for example with KEDA) would
add pods here, up to one per partition (3).

## 5. Poison messages: a producer sends bad events

**Inject:** `./scripts/chaos/poison-messages.sh inject` writes straight to the topics with `rpk`, bypassing orders:
3 bad messages to `order.created` (not JSON; wrong shape; right shape with a negative amount and a lowercase currency)
and 2 to `payment.done` (not JSON; wrong shape). All use the key `chaos-poison`.

**Expected (observed)**

| Signal | Evidence |
|---|---|
| Alerts | `InvalidMessagesReceived` for `payments` and for `notifications`, firing within 1 min (no wait period) |
| Metrics | `payments_invalid_messages_total` +3; `notifications_messages_processed_total{result="skipped_invalid"}` +2; lag stays 0; no restarts; orders unaffected |
| Logs | `{namespace="sentinel"} \|= "skipping invalid"`: 5 WARN lines. payments lists the pydantic errors (`json_invalid`, `missing`, `greater_than_equal`, `string_pattern_mismatch`); notifications lists the schema errors (`event_id: Invalid input: expected string, received undefined` ...) or `not JSON` |
| Traces | TraceQL `{ status = error }`: 5 one-span traces, `order.created process` (3) and `payment.done process` (2), each a few ms |

**Root cause:** something other than orders wrote invalid events. The consumers log, count and skip each one, so
nothing stops; the alert tells you to find the producer.

**Recover:** nothing to undo (`recover` only prints this). The bad messages are committed and skipped;
`InvalidMessagesReceived` clears 10 minutes after the last one.

## 6. Broker outage: Redpanda is down

**Inject:** `./scripts/chaos/broker-outage.sh inject` scales the Redpanda StatefulSet to 0 and waits for the pod to go.
Data is on a PersistentVolume, so topics and consumer offsets survive.

**What the services do:** orders saves each order and its event in one database transaction (the outbox), so it
keeps accepting orders; the relay retries every 0.5 s (each try waits up to 5 s). payments and notifications
reconnect in-process, waiting 1 s, 2 s, 4 s ... up to 30 s between tries: alive, not ready, no restarts.

**Expected (observed)**

| Signal | Evidence |
|---|---|
| Alerts | `OutboxBacklog` pending after about 1.75 min, firing after about 3.75 min (oldest unsent event over 60 s, for 2 min) |
| Metrics | `POST /orders` stays at 2/s, all 201, no 5xx; `outbox_pending_events` grows by about 2/s and `outbox_oldest_pending_age_seconds` by 1 per second; consumer lag metrics disappear (Redpanda reports them) |
| Pods | 0 restarts. notifications `0/1` within a minute. A payments pod that was already connected stays `1/1`: its Kafka client retries inside the library and never tells the worker. The autoscaler may add payments pods on the reconnect CPU spike; they wait `0/1` |
| Logs | orders WARN `outbox relay failed, will retry: Send failed` (about 11 per minute); notifications and any new payments pod WARN `lost connection to Redpanda, reconnecting` with `failures` and `retry_in_ms` (payments: `KafkaConnectionError ... Unable to bootstrap`; notifications: `PLT_KFK_MULTIPLE: joinGroup failed ...`) |
| Traces | No new `order.created send` spans until recovery |

**Root cause:** the message broker is down. Customers can still order; payments and receipts are delayed, not lost.

**Recover:** `./scripts/chaos/broker-outage.sh recover`. Observed: the outbox (about 600 events after 5 minutes) and
the consumer backlogs were empty about 80 s after the command (Redpanda startup, plus up to 30 s until each consumer's
next reconnect try). Extra payments pods scaled away within a minute. Smoke test passed.

**History:** the first run (before the in-process reconnect) showed the same weakness as the first S3 outage:
notifications' worker stopped, `/healthz` failed and Kubernetes killed it 7 times in 8 minutes; a payments pod added by the
autoscaler could not connect at startup, was killed the same way, and left its Kafka clients unclosed. Only
`OutboxBacklog` and `PodRestarted` fired. With the fix, the re-run had 0 restarts and recovered on its own.

**Known gap:** a connected payments pod reports ready while Redpanda is down (see Pods above). It recovers by itself.
