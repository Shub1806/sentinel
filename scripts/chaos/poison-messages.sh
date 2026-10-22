#!/usr/bin/env bash
# Chaos scenario 5: a producer sends bad events. Writes invalid messages straight to the
# order.created and payment.done topics. The consumers should log and skip them, not crash.
# Usage: ./scripts/chaos/poison-messages.sh inject | recover
set -euo pipefail
NS=sentinel
KEY=chaos-poison

produce() {
  kubectl -n "$NS" exec -i redpanda-0 -- rpk topic produce "$1" -f '%k %v\n'
}

case "${1:-}" in
  inject)
    # order.created: not JSON, wrong shape, right shape with bad values.
    produce order.created <<MSGS
$KEY this is not json
$KEY {"hello":"world"}
$KEY {"event_id":"00000000-0000-4000-8000-000000000001","event_type":"order.created","occurred_at":"2026-01-01T00:00:00Z","order_id":"00000000-0000-4000-8000-000000000002","customer_id":"chaos","amount_cents":-5,"currency":"usd"}
MSGS
    # payment.done: not JSON, wrong shape.
    produce payment.done <<MSGS
$KEY this is not json
$KEY {"event_type":"payment.done"}
MSGS
    echo "Injected: 3 bad messages to order.created, 2 to payment.done"
    ;;
  recover)
    echo "Nothing to undo: the bad messages were skipped and committed."
    echo "InvalidMessagesReceived clears 10 minutes after the last one."
    ;;
  *) echo "Usage: $0 inject | recover" >&2; exit 1 ;;
esac
