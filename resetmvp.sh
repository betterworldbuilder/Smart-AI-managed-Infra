#!/usr/bin/env bash
#
#   ./resetmvp.sh             wipe the Copilot database, deployments and generated workloads
#   ./resetmvp.sh --cluster   also delete and recreate the kind cluster

set -uo pipefail
source "$(dirname "$0")/scripts/lib.sh"

cd "$ROOT_DIR"
MVP_ENV="${ROOT_DIR}/deployments/mvp/.env.mvp"
CLUSTER="$(env_value "$MVP_ENV" KIND_CLUSTER_NAME gpu-native-mvp)"
export KUBECONFIG="${ROOT_DIR}/deployments/mvp/kubeconfig"

WITH_CLUSTER=0
for arg in "$@"; do
  [ "$arg" = "--cluster" ] && WITH_CLUSTER=1
done

if [ "$WITH_CLUSTER" = "1" ]; then
  say "This deletes the kind cluster '${CLUSTER}' and recreates everything."
  confirm "Continue?" || { say "Cancelled."; exit 0; }
  exec "${ROOT_DIR}/startmvp.sh" --recreate -y
fi

say "This resets the Copilot database, deployments, demo data and every workload"
say "the Governor created. The kind cluster itself is preserved."
confirm "Continue?" || { say "Cancelled."; exit 0; }

command -v kubectl >/dev/null 2>&1 || die "kubectl is required"

info "Deleting workload namespaces created by the Governor"
NAMESPACES="$(kubectl get ns -l aiinfra.io/workload -o name 2>/dev/null)"
if [ -z "$NAMESPACES" ]; then
  # Generated namespaces are prefixed ai-; the platform's own is 'aiinfra'.
  NAMESPACES="$(kubectl get ns -o name 2>/dev/null | grep -E 'namespace/ai-' || true)"
fi
if [ -n "$NAMESPACES" ]; then
  # shellcheck disable=SC2086
  kubectl delete $NAMESPACES --wait=false >/dev/null 2>&1 || true
  ok "removed: $(printf '%s' "$NAMESPACES" | tr '\n' ' ')"
else
  ok "no generated workload namespaces"
fi

info "Wiping the Copilot database"
kubectl -n aiinfra exec statefulset/postgres -- \
  psql -U gpuinfra -d gpuinfra -c 'TRUNCATE TABLE records' >/dev/null 2>&1 \
  && ok "records table truncated" \
  || warn "could not truncate the records table (is postgres running?)"

info "Clearing the generated GitOps tree"
rm -rf "${ROOT_DIR}/deployments/mvp/flux/workloads/generated"
mkdir -p "${ROOT_DIR}/deployments/mvp/flux/workloads/generated"

info "Restarting the backend"
kubectl -n aiinfra rollout restart deployment/backend >/dev/null 2>&1 || true
kubectl -n aiinfra rollout status deployment/backend --timeout=180s >/dev/null 2>&1 || true

ok "MVP reset. UI: $(public_url 3000)"
