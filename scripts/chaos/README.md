   # Chaos scenarios

   Each script takes `inject` or `recover`. Start the order generator first, so the SLO alerts
   have enough traffic: `kubectl apply -f infra/chaos/loadgen.yaml`.

   | # | Scenario | Script |
   |---|---|---|
   | 1 | Bad deploy: payments crash-loops | `payments-bad-config.sh` |

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
