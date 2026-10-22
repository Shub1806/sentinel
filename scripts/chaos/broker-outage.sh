#!/usr/bin/env bash
# Chaos scenario 6: Redpanda goes down. orders keeps accepting orders (they are saved with
# their event in the outbox), but nothing reaches payments or notifications until it is back.
# Data is on a PersistentVolume, so topics and consumer offsets survive.
# Usage: ./scripts/chaos/broker-outage.sh inject | recover
set -euo pipefail
NS=sentinel

case "${1:-}" in
  inject)
    kubectl -n "$NS" scale statefulset/redpanda --replicas=0
    kubectl -n "$NS" wait --for=delete pod/redpanda-0 --timeout=120s
    echo "Injected: Redpanda scaled to 0"
    ;;
  recover)
    kubectl -n "$NS" scale statefulset/redpanda --replicas=1
    kubectl -n "$NS" rollout status statefulset/redpanda --timeout=300s
    ;;
  *) echo "Usage: $0 inject | recover" >&2; exit 1 ;;
esac
