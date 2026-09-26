#!/usr/bin/env bash
#
#   ./stopmvp.sh              stop the application, keep the kind cluster
#   ./stopmvp.sh --destroy    delete the cluster, its volumes and generated GitOps files

set -uo pipefail
source "$(dirname "$0")/scripts/lib.sh"

cd "$ROOT_DIR"
MVP_ENV="${ROOT_DIR}/deployments/mvp/.env.mvp"
CLUSTER="$(env_value "$MVP_ENV" KIND_CLUSTER_NAME gpu-native-mvp)"
export KUBECONFIG="${ROOT_DIR}/deployments/mvp/kubeconfig"

DESTROY=0
for arg in "$@"; do
  [ "$arg" = "--destroy" ] && DESTROY=1
done

if ! command -v kind >/dev/null 2>&1; then
  die "kind is not installed; nothing to stop"
fi

if ! kind get clusters 2>/dev/null | grep -qx "$CLUSTER"; then
  warn "kind cluster '${CLUSTER}' does not exist"
  exit 0
fi

if [ "$DESTROY" = "1" ]; then
  say "This deletes:"
  say "  - the kind cluster '${CLUSTER}' and every workload in it"
  say "  - its volumes (PostgreSQL data)"
  say "  - generated GitOps resources under deployments/mvp/flux/workloads/generated"
  if ! confirm "Continue?"; then
    say "Cancelled."
    exit 0
  fi
  info "Deleting the kind cluster"
  kind delete cluster --name "$CLUSTER"
  rm -f "${ROOT_DIR}/deployments/mvp/kubeconfig"
  rm -rf "${ROOT_DIR}/deployments/mvp/flux/workloads/generated"
  mkdir -p "${ROOT_DIR}/deployments/mvp/flux/workloads/generated"
  ok "MVP destroyed"
  exit 0
fi

info "Scaling the application down (the cluster stays up)"
kubectl -n aiinfra scale deployment/backend deployment/frontend deployment/opa --replicas=0 \
  >/dev/null 2>&1 || warn "could not scale the application down"
kubectl -n aiinfra scale statefulset/postgres --replicas=0 >/dev/null 2>&1 || true
kubectl -n aiinfra scale deployment/redis --replicas=0 >/dev/null 2>&1 || true
ok "MVP application stopped; workloads you deployed through it are untouched"
say "Bring it back with ./startmvp.sh, or remove everything with ./stopmvp.sh --destroy"
