#!/usr/bin/env bash
# Chaos scenario 4: payments becomes slow. Each message waits PROCESSING_DELAY_MS before it is
# handled, so one pod manages fewer messages than arrive and lag builds up. Waiting uses no CPU,
# so the CPU-based autoscaler does not add pods.
# Usage: ./scripts/chaos/slow-consumer.sh inject [delay-ms] | recover
set -euo pipefail
NS=sentinel

case "${1:-}" in
  inject)
    delay="${2:-2000}"
    kubectl -n "$NS" set env deploy/payments "PROCESSING_DELAY_MS=$delay"
    kubectl -n "$NS" rollout status deploy/payments --timeout=120s
    echo "Injected: payments waits ${delay} ms per message"
    ;;
  recover)
    kubectl -n "$NS" set env deploy/payments PROCESSING_DELAY_MS-
    kubectl -n "$NS" rollout status deploy/payments --timeout=120s
    ;;
  *)
    echo "Usage: $0 inject [delay-ms] | recover" >&2
    exit 1
    ;;
esac
