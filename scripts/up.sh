#!/usr/bin/env bash
# Build Sentinel from nothing: images -> cluster -> load images -> platform.
# Usage: ./scripts/up.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CLUSTER=sentinel
SERVICES=(orders payments notifications)

step() { printf '\n==> %s\n' "$*"; }

# The LocalStack token lives in .env, which is never committed.
if [[ -f "$ROOT/.env" ]]; then
  set -a; source "$ROOT/.env"; set +a
fi
: "${LOCALSTACK_AUTH_TOKEN:?Set LOCALSTACK_AUTH_TOKEN in .env}"
export TF_VAR_localstack_auth_token="$LOCALSTACK_AUTH_TOKEN"

step "1/4 Building service images"
images=()
for s in "${SERVICES[@]}"; do
  docker build -q -t "sentinel/$s:dev" "$ROOT/services/$s"
  images+=("sentinel/$s:dev")
done

step "2/4 Creating the cluster (Terraform: cluster stack)"
terraform -chdir="$ROOT/infra/terraform/cluster" init -input=false > /dev/null
terraform -chdir="$ROOT/infra/terraform/cluster" apply -input=false -auto-approve

step "3/4 Loading images into the cluster"
k3d image import "${images[@]}" -c "$CLUSTER"

step "4/4 Deploying the platform (Terraform: platform stack)"
terraform -chdir="$ROOT/infra/terraform/platform" init -input=false > /dev/null
terraform -chdir="$ROOT/infra/terraform/platform" apply -input=false -auto-approve

kubectl config set-context "k3d-$CLUSTER" --namespace=sentinel > /dev/null
step "Done"
kubectl get pods
