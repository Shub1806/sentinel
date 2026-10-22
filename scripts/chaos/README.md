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

**What notifications does:** a failed S3 write throws, the consumer loop stops, `/healthz` turns 503 and Kubernetes
restarts the pod (about every 2 minutes). Each new pod fails on the same uncommitted message and stops again.

**Expected (observed)**

| Signal | Evidence |
|---|---|
| Alerts | `PodRestarted` (about 2 min); `ConsumerLagHigh` for `notifications` (about 15 min); `KubePodNotReady`, `KubeDeploymentReplicasMismatch` (15 min); `KubePodCrashLooping` pending. **`NotificationErrors` does not fire** |
| Metrics | notifications lag grows by about 120 per minute (the order rate); orders and payments unaffected; `increase(notifications_message_errors_total[10m])` = **0**: the pod is never ready, so Prometheus doesn't scrape it, and each restart resets the counter |
| Logs | **The only place with the cause:** notifications ERROR `worker stopped with an error`, `connect ECONNREFUSED <localstack ip>:4566`, once per restart |
| Traces | `payment.done process` and `S3 PutObject` spans with an error status (one per restart) |

**Root cause:** S3 is unreachable. Customers are not affected; receipts are delayed.

**Recover:** `./scripts/chaos/s3-outage.sh recover`. notifications only resumes after its current restart back-off
(up to 5 minutes; `kubectl rollout restart deploy/notifications` skips it), then drains the backlog in under a minute.

**Weakness found:** one failed write takes the whole consumer down, which hides the error from metrics and adds
restart back-off to the recovery time. Fix: retry inside the process with back-off, keep the pod alive and visible.
