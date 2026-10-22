# Chaos scenarios

Each script takes `inject` or `recover`. Start the order generator first, so the SLO alerts
have enough traffic: `kubectl apply -f infra/chaos/loadgen.yaml`.

| # | Scenario | Script |
|---|---|---|
| 1 | Bad deploy: payments crash-loops | `payments-bad-config.sh` |
| 2 | Database outage | `database-outage.sh` |
| 3 | S3 outage | `s3-outage.sh` |

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
