#!/usr/bin/env bash
# Build Sentinel from nothing: images -> cluster -> load images -> platform.
# Usage: ./scripts/up.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CLUSTER=sentinel
SERVICES=(orders payments notifications agent)

step() { printf '\n==> %s\n' "$*"; }

# The LocalStack token lives in .env, which is never committed.
if [[ -f "$ROOT/.env" ]]; then
  set -a; source "$ROOT/.env"; set +a
fi
: "${LOCALSTACK_AUTH_TOKEN:?Set LOCALSTACK_AUTH_TOKEN in .env}"
export TF_VAR_localstack_auth_token="$LOCALSTACK_AUTH_TOKEN"

step "1/4 Building service images"
# Each image is tagged with the first 12 characters of its content ID.
# Same code -> same tag -> nothing restarts. Changed code -> new tag -> Kubernetes rolls out new pods.
images=()
tag_map=""
for s in "${SERVICES[@]}"; do
  # --provenance=false: Docker otherwise adds a build-time note to the image, which
  # changes the ID on every build even when the code is identical.
  id="$(docker build -q --provenance=false -t "sentinel/$s:dev" "$ROOT/services/$s")"
  tag="${id#sha256:}"
  tag="${tag:0:12}"
  docker tag "sentinel/$s:dev" "sentinel/$s:$tag"
  images+=("sentinel/$s:$tag")
  tag_map+="${tag_map:+,}$s=\"$tag\""
  printf '%-14s %s\n' "$s" "$tag"
done
export TF_VAR_image_tags="{$tag_map}"

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
