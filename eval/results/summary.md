| Scenario | Runs | Agent: cause | Agent: action | Baseline: cause | Baseline: action | Time to report (median) |
|---|---|---|---|---|---|---|
| Bad deploy: payments crash-loops | 3 | 100% (3/3) | 100% (3/3) | 100% (3/3) | 100% (3/3) | 1 min 45 s |
| Database outage | 3 | 100% (3/3) | 100% (3/3) | 100% (3/3) | 100% (3/3) | 7 min 35 s |
| S3 outage | 3 | 100% (3/3) | 100% (3/3) | 100% (3/3) | 100% (3/3) | 7 min 14 s |
| Slow consumer: payments falls behind | 3 | 100% (3/3) | 100% (3/3) | 100% (3/3) | 100% (3/3) | 17 min 48 s |
| Poison messages | 3 | 0% (0/3) | 0% (0/3) | 100% (3/3) | 100% (3/3) | 3 min 42 s |
| Broker outage | 3 | 100% (3/3) | 100% (3/3) | 100% (3/3) | 100% (3/3) | 5 min 35 s |
| **All** | 18 | 83% (15/18) | 83% (15/18) | 100% (18/18) | 100% (18/18) | |

| Run | Agent's cause | Agent suggested | Baseline suggested | Timed out |
|---|---|---|---|---|
| bad-deploy-1 | New deployment rollout added invalid environment variable MAX_AMOUNT_CENTS=abc causing application startup failure ✓ | rollout_undo payments ✓ | rollout_undo payments ✓ |  |
| bad-deploy-2 | Environment variable MAX_AMOUNT_CENTS set to 'abc' in latest payments rollout ✓ | rollout_undo payments ✓ | rollout_undo payments ✓ |  |
| bad-deploy-3 | Environment variable MAX_AMOUNT_CENTS set to invalid string 'abc' in latest rollout ✓ | rollout_undo payments ✓ | rollout_undo payments ✓ |  |
| broker-outage-1 | Redpanda cluster not running or DNS resolution failure for 'redpanda' ✓ | manual ✓ | manual ✓ |  |
| broker-outage-2 | Redpanda service missing or misconfigured in the sentinel namespace ✓ | manual ✓ | manual ✓ |  |
| broker-outage-3 | Missing Kubernetes service for Redpanda cluster ✓ | manual ✓ | manual ✓ |  |
| db-outage-1 | Postgres cluster (orders-db) is in hibernation state and not responding ✓ | manual ✓ | manual ✓ |  |
| db-outage-2 | Postgres cluster (orders-db) is in hibernation state, leading to connection timeouts ✓ | manual ✓ | manual ✓ |  |
| db-outage-3 | Postgres cluster 'orders-db' is hibernated (shut down on purpose) ✓ | manual ✓ | manual ✓ |  |
| poison-1 | Payments deployment's latest rollout removed the MAX_AMOUNT_CENTS environment variable, causing invalid order amounts... ✗ | rollout_undo payments ✗ | none ✓ |  |
| poison-2 | Removal of MAX_AMOUNT_CENTS environment variable in the latest payments rollout ✗ | rollout_undo payments ✗ | none ✓ |  |
| poison-3 | Removal of MAX_AMOUNT_CENTS environment variable in payments rollout ✗ | rollout_undo payments ✗ | none ✓ |  |
| s3-outage-1 | LocalStack service is not running ✓ | manual ✓ | manual ✓ |  |
| s3-outage-2 | LocalStack pod is not running correctly (connection refused) ✓ | manual localstack ✓ | manual ✓ |  |
| s3-outage-3 | LocalStack pods are not healthy (liveness/readiness probes failing) ✓ | manual localstack ✓ | manual ✓ |  |
| slow-consumer-1 | New environment variable PROCESSING_DELAY_MS=2000 in the payments deployment rollout caused readiness probe failures ✓ | rollout_undo payments ✓ | manual ✓ |  |
| slow-consumer-2 | New environment variable PROCESSING_DELAY_MS=2000 added in the payments deployment rollout ✓ | rollout_undo payments ✓ | manual ✓ |  |
| slow-consumer-3 | The latest rollout added the environment variable PROCESSING_DELAY_MS=2000 to the payments deployment ✓ | rollout_undo payments ✓ | manual ✓ |  |
