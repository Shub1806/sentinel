#!/usr/bin/env bash
# Chaos scenario 2: the orders database goes down. CloudNativePG "hibernation" stops the Postgres
# pod but keeps its volume, so no data is lost and recovery is clean.
# Usage: ./scripts/chaos/database-outage.sh inject|recover
set -euo pipefail
NS=sentinel
CLUSTER=orders-db

case "${1:-}" in
  inject)
    kubectl -n "$NS" annotate cluster "$CLUSTER" --overwrite cnpg.io/hibernation=on
    echo "Injected: $CLUSTER is hibernating (Postgres stopped)"
    ;;
  recover)
    kubectl -n "$NS" annotate cluster "$CLUSTER" --overwrite cnpg.io/hibernation=off
    # The Cluster's Ready condition can still be stale at this point, so wait for the Postgres pod itself.
    echo "Waiting for the Postgres pod to come back..."
    for _ in $(seq 1 60); do
      kubectl -n "$NS" get pod -l "cnpg.io/cluster=$CLUSTER" -o name | grep -q . && break
      sleep 2
    done
    kubectl -n "$NS" wait pod -l "cnpg.io/cluster=$CLUSTER" --for=condition=Ready --timeout=300s
    ;;
  *)
    echo "Usage: $0 inject|recover" >&2
    exit 1
    ;;
esac
