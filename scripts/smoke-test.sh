#!/usr/bin/env bash
# End-to-end check: place an order, then wait until its receipt appears in S3.
# Passes (exit 0) only if the whole chain works: orders -> Redpanda -> payments -> notifications -> S3.
# Usage: ./scripts/smoke-test.sh
set -euo pipefail

NS=sentinel
PORT=18080   # a local port unlikely to be in use

# Temporary tunnel to the orders Service, closed automatically when the script ends.
kubectl -n "$NS" port-forward svc/orders "$PORT:8080" > /dev/null 2>&1 &
PF_PID=$!
trap 'kill "$PF_PID" 2> /dev/null || true' EXIT

for _ in $(seq 1 30); do
  curl -sf "localhost:$PORT/actuator/health" > /dev/null && break
  sleep 1
done
curl -sf "localhost:$PORT/actuator/health" > /dev/null \
  || { echo "FAIL: orders is not reachable" >&2; exit 1; }

response=$(curl -sf -X POST "localhost:$PORT/orders" -H 'Content-Type: application/json' \
  -d '{"customer_id":"smoke-test","amount_cents":1234,"currency":"USD"}')
order_id=$(printf '%s' "$response" | python3 -c 'import json, sys; print(json.load(sys.stdin)["id"])')
echo "Created order $order_id"

for i in $(seq 1 30); do
  if kubectl -n "$NS" exec deploy/localstack -- \
       awslocal s3api head-object --bucket sentinel-receipts --key "receipts/$order_id.json" \
       > /dev/null 2>&1; then
    echo "PASS: receipt for $order_id is in S3 (attempt $i)"
    exit 0
  fi
  sleep 1
done

echo "FAIL: no receipt for $order_id after 30 attempts" >&2
exit 1
