   #!/usr/bin/env bash
   # Chaos scenario 1: a bad deploy. payments gets an invalid setting, so the new pod crashes
   # at startup and the rollout gets stuck. The old pod keeps running, so orders still flow.
   # Usage: ./scripts/chaos/payments-bad-config.sh inject|recover
   set -euo pipefail
   NS=sentinel

   case "${1:-}" in
     inject)
       kubectl -n "$NS" set env deploy/payments MAX_AMOUNT_CENTS=abc
       echo "Injected: payments rollout with MAX_AMOUNT_CENTS=abc"
       ;;
     recover)
       # Removing the variable brings the pod template back to the working version.
       kubectl -n "$NS" set env deploy/payments MAX_AMOUNT_CENTS-
       kubectl -n "$NS" rollout status deploy/payments --timeout=120s
       ;;
     *)
       echo "Usage: $0 inject|recover" >&2
       exit 1
       ;;
   esac
