#!/usr/bin/env bash
# Remove everything, in the reverse order of up.sh.
# Usage: ./scripts/down.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

step() { printf '\n==> %s\n' "$*"; }

# Terraform needs the same inputs to destroy as it did to create.
if [[ -f "$ROOT/.env" ]]; then
  set -a; source "$ROOT/.env"; set +a
fi
export TF_VAR_localstack_auth_token="${LOCALSTACK_AUTH_TOKEN:-}"

step "1/2 Removing everything inside the cluster (platform stack)"
terraform -chdir="$ROOT/infra/terraform/platform" destroy -input=false -auto-approve

step "2/2 Deleting the cluster (cluster stack)"
terraform -chdir="$ROOT/infra/terraform/cluster" destroy -input=false -auto-approve

step "Done: everything removed"
