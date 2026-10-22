#!/usr/bin/env bash
# Chaos scenario 3: S3 (LocalStack) goes down. notifications can't save receipts.
# LocalStack keeps S3 in memory: existing receipts are lost, and the bucket is
# created again when it starts.
# Usage: ./scripts/chaos/s3-outage.sh inject|recover
set -euo pipefail
NS=sentinel

case "${1:-}" in
  inject)
    kubectl -n "$NS" scale deploy/localstack --replicas=0
    echo "Injected: LocalStack (S3) scaled to 0"
    ;;
  recover)
    kubectl -n "$NS" scale deploy/localstack --replicas=1
    kubectl -n "$NS" rollout status deploy/localstack --timeout=300s
    ;;
  *)
    echo "Usage: $0 inject|recover" >&2
    exit 1
    ;;
esac
