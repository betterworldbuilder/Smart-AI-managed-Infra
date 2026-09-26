#!/usr/bin/env bash
#
# Start the local MVP: a REAL kind Kubernetes cluster running the same
# application, with real Flux and real Kubernetes workloads.
#
#   ./startmvp.sh              create (or reuse) the cluster and deploy
#   ./startmvp.sh --recreate   delete the cluster first
#   ./startmvp.sh --no-flux    skip the Flux installation
#
# OpenStack, Genestack, Nova and Ceph stay simulated in this phase -- the UI
# says so on every screen.

set -uo pipefail
source "$(dirname "$0")/scripts/lib.sh"

cd "$ROOT_DIR"

RECREATE=0
WITH_FLUX=1
WITH_CNPG=1
for arg in "$@"; do
  case "$arg" in
    --recreate) RECREATE=1 ;;
    --no-flux) WITH_FLUX=0 ;;
    --no-cnpg) WITH_CNPG=0 ;;
    -y | --yes) export ASSUME_YES=1 ;;
    -h | --help)
      say "Usage: ./startmvp.sh [--recreate] [--no-flux] [--no-cnpg]"
      exit 0
      ;;
  esac
done

MVP_ENV="${ROOT_DIR}/deployments/mvp/.env.mvp"
ensure_env_file "$MVP_ENV"
CLUSTER="$(env_value "$MVP_ENV" KIND_CLUSTER_NAME gpu-native-mvp)"
WORKERS="$(env_value "$MVP_ENV" KIND_WORKERS 2)"
KUBECONFIG_PATH="${ROOT_DIR}/deployments/mvp/kubeconfig"
KIND_CONFIG="${ROOT_DIR}/deployments/mvp/kind/cluster.yaml"

rule
say "${C_BOLD}GPU NATIVE INFRA - LOCAL MVP (kind)${C_RESET}"
rule

# --- 1. prerequisites -------------------------------------------------------
info "Checking prerequisites"
require_docker
ok "docker"

install_binary() {
  # install_binary NAME URL
  local name="$1" url="$2" target="${HOME}/.local/bin"
  mkdir -p "$target"
  info "Installing ${name} into ${target}"
  if curl -fsSL "$url" -o "${target}/${name}"; then
    chmod +x "${target}/${name}"
    export PATH="${target}:${PATH}"
    ok "${name} installed"
    return 0
  fi
  return 1
}

ARCH="$(uname -m)"
case "$ARCH" in
  x86_64 | amd64) ARCH=amd64 ;;
  aarch64 | arm64) ARCH=arm64 ;;
esac
OS_NAME="$(uname -s | tr '[:upper:]' '[:lower:]')"

if ! command -v kind >/dev/null 2>&1; then
  warn "kind is not installed"
  if [ "$OS_NAME" = "linux" ] && confirm "Install kind automatically into ~/.local/bin?"; then
    install_binary kind \
      "https://kind.sigs.k8s.io/dl/v0.25.0/kind-${OS_NAME}-${ARCH}" ||
      die "could not install kind"
  else
    die "Install kind: https://kind.sigs.k8s.io/docs/user/quick-start/#installation"
  fi
fi
ok "kind $(kind version 2>/dev/null | head -n1)"

if ! command -v kubectl >/dev/null 2>&1; then
  warn "kubectl is not installed"
  if [ "$OS_NAME" = "linux" ] && confirm "Install kubectl automatically into ~/.local/bin?"; then
    KVER="$(curl -fsSL https://dl.k8s.io/release/stable.txt || echo v1.31.3)"
    install_binary kubectl \
      "https://dl.k8s.io/release/${KVER}/bin/${OS_NAME}/${ARCH}/kubectl" ||
      die "could not install kubectl"
  else
    die "Install kubectl: https://kubernetes.io/docs/tasks/tools/"
  fi
fi
ok "kubectl $(kubectl version --client -o json 2>/dev/null | pyjson "data['clientVersion']['gitVersion']" || echo present)"

if ! command -v helm >/dev/null 2>&1; then
  warn "helm is not installed -- it is optional for this MVP (manifests are plain YAML)"
else
  ok "helm $(helm version --short 2>/dev/null)"
fi

# --- 1a. kernel limits --------------------------------------------------------
# A multi-node kind cluster opens a lot of inotify instances. The default on
# WSL and many distros (128) is not enough and kubeadm fails with a confusing
# "context deadline exceeded" during control-plane bootstrap.
INOTIFY_INSTANCES="$(sysctl -n fs.inotify.max_user_instances 2>/dev/null || echo 0)"
INOTIFY_WATCHES="$(sysctl -n fs.inotify.max_user_watches 2>/dev/null || echo 0)"
if [ "${INOTIFY_INSTANCES:-0}" -lt 512 ] || [ "${INOTIFY_WATCHES:-0}" -lt 524288 ]; then
  warn "inotify limits are too low for a multi-node kind cluster"
  say "      current: max_user_instances=${INOTIFY_INSTANCES} max_user_watches=${INOTIFY_WATCHES}"
  say "      needed:  max_user_instances>=512 max_user_watches>=524288"
  if confirm "Raise them now with sudo (not persisted across reboots)?"; then
    sudo sysctl -w fs.inotify.max_user_instances=1024 fs.inotify.max_user_watches=1048576 \
      >/dev/null 2>&1 && ok "inotify limits raised" || warn "could not raise them"
  else
    say ""
    say "  Run this yourself, then retry:"
    say "    sudo sysctl -w fs.inotify.max_user_instances=1024 fs.inotify.max_user_watches=1048576"
    say "  To make it permanent:"
    say "    echo -e 'fs.inotify.max_user_instances=1024\\nfs.inotify.max_user_watches=1048576' \\"
    say "      | sudo tee /etc/sysctl.d/99-kind.conf && sudo sysctl --system"
    die "kind would fail to bootstrap with the current limits"
  fi
fi
ok "inotify limits ok"

# --- 1b. the POC and the MVP want the same host ports -------------------------
# Different docker projects and cluster names keep the *resources* separate,
# but ports 3000/8000/9090 can only belong to one of them at a time.
POC_RUNNING="$(docker ps --filter "label=com.docker.compose.project=${POC_PROJECT}" -q | wc -l | tr -d ' ')"
if [ "${POC_RUNNING:-0}" -gt 0 ]; then
  warn "the POC is running and holds ports 3000 / 8000 / 9090"
  if confirm "Stop the POC first (its data is preserved)?"; then
    ENV_FILE="${ROOT_DIR}/deployments/poc/.env.poc" \
      COMPOSE_PROJECT="${POC_PROJECT}" \
      COMPOSE_PROFILES_ARGS="--profile observability" compose stop >/dev/null 2>&1
    ok "POC stopped (./startpoc.sh brings it back)"
  else
    die "cannot continue while the POC holds those ports"
  fi
fi

# --- 2. the cluster ----------------------------------------------------------
if [ "$WORKERS" != "2" ]; then
  info "Regenerating the kind configuration for ${WORKERS} worker(s)"
  {
    sed -n '1,/^  - role: worker$/p' "$KIND_CONFIG" | sed '$d'
    for _ in $(seq 1 "$WORKERS"); do
      printf '  - role: worker\n    labels:\n      aiinfra.io/pool: general\n'
    done
  } >"${KIND_CONFIG}.tmp" && mv "${KIND_CONFIG}.tmp" "$KIND_CONFIG"
fi

if kind get clusters 2>/dev/null | grep -qx "$CLUSTER"; then
  if [ "$RECREATE" = "1" ]; then
    info "Deleting the existing cluster ${CLUSTER}"
    kind delete cluster --name "$CLUSTER"
    CREATE=1
  else
    ok "reusing the existing kind cluster '${CLUSTER}'"
    CREATE=0
  fi
else
  CREATE=1
fi

diagnose_kind_failure() {
  say ""
  fail "kind could not bootstrap the cluster."
  say ""
  STORAGE_DRIVER="$(docker info --format '{{.Driver}}' 2>/dev/null)"
  say "  Docker storage driver: ${STORAGE_DRIVER:-unknown}"
  case "$STORAGE_DRIVER" in
    overlayfs)
      say ""
      say "  ${C_BOLD}Likely cause:${C_RESET} Docker's newer 'overlayfs' storage driver (Docker 29+)"
      say "  cannot host kind's nested containerd snapshotter. The control plane"
      say "  never starts and kubeadm times out waiting for the API server."
      say ""
      say "  Confirm with:"
      say "    kind create cluster --name probe --retain"
      say "    docker exec probe-control-plane journalctl -u containerd | grep 'commit snapshot'"
      say ""
      say "  Fixes, in order of preference:"
      say "    1. Switch Docker back to the 'overlay2' driver:"
      say "         sudo tee /etc/docker/daemon.json <<'EOF'"
      say "         { \"storage-driver\": \"overlay2\" }"
      say "         EOF"
      say "         sudo systemctl restart docker      # WSL: sudo service docker restart"
      say "       NOTE: changing the storage driver hides existing images and"
      say "       containers built under the old driver. Back up anything you need."
      say "    2. Use Docker Desktop's own Kubernetes, or a Linux VM, for the MVP."
      say "    3. Stay on the POC: ./startpoc.sh gives you the whole workflow"
      say "       with a simulated Kubernetes layer."
      ;;
    *)
      say ""
      say "  Check:"
      say "    - inotify limits (see above)"
      say "    - Docker Desktop resources: 4+ CPUs, 8+ GB RAM"
      say "    - docker logs ${CLUSTER}-control-plane | tail -50"
      ;;
  esac
  say ""
  say "  docs/troubleshooting.md has the full list."
  exit 1
}

if [ "$CREATE" = "1" ]; then
  info "Creating the kind cluster '${CLUSTER}' (1 control plane + ${WORKERS} workers)"
  kind create cluster --name "$CLUSTER" --config "$KIND_CONFIG" --wait 180s \
    || diagnose_kind_failure
fi

info "Writing kubeconfig"
kind get kubeconfig --name "$CLUSTER" >"$KUBECONFIG_PATH"
export KUBECONFIG="$KUBECONFIG_PATH"
ok "kubeconfig: ${KUBECONFIG_PATH}"

kubectl cluster-info >/dev/null 2>&1 || die "the Kubernetes API is not answering"
NODES="$(kubectl get nodes --no-headers 2>/dev/null | wc -l | tr -d ' ')"
ok "cluster reachable, ${NODES} node(s)"

# --- 3. Flux ------------------------------------------------------------------
FLUX_STATE="skipped"
if [ "$WITH_FLUX" = "1" ]; then
  info "Installing FluxCD"
  if kubectl get crd kustomizations.kustomize.toolkit.fluxcd.io >/dev/null 2>&1; then
    ok "Flux is already installed"
    FLUX_STATE="installed"
  elif command -v flux >/dev/null 2>&1 && flux install --components=source-controller,kustomize-controller >/dev/null 2>&1; then
    ok "Flux installed with the flux CLI"
    FLUX_STATE="installed"
  elif kubectl apply -f \
    "https://github.com/fluxcd/flux2/releases/download/v2.4.0/install.yaml" >/dev/null 2>&1; then
    ok "Flux installed from the release manifest"
    FLUX_STATE="installed"
  else
    warn "could not install Flux (no network?). The MVP continues in compat mode:"
    warn "manifests are still committed to the GitOps tree and applied to the cluster."
    FLUX_STATE="unavailable"
  fi
  if [ "$FLUX_STATE" = "installed" ]; then
    kubectl -n flux-system wait --for=condition=Available deployment --all --timeout=180s \
      >/dev/null 2>&1 || warn "Flux controllers are still starting"
  fi
fi

# --- 4. platform namespaces, storage, policies --------------------------------
info "Applying platform manifests"
kubectl apply -f "${ROOT_DIR}/deployments/mvp/kubernetes/namespaces/namespaces.yaml" >/dev/null
kubectl apply -f "${ROOT_DIR}/deployments/mvp/kubernetes/storage/storageclass.yaml" >/dev/null
ok "namespaces and the ceph-rbd StorageClass (local-path backed) are in place"

info "Loading policies into the cluster"
kubectl -n aiinfra create configmap aiinfra-policies \
  --from-file="${ROOT_DIR}/policies/rego/" \
  --dry-run=client -o yaml | kubectl apply -f - >/dev/null
ok "rego policies loaded (same files the POC uses)"

# --- 5. optional: CloudNativePG so the database path is real -------------------
if [ "$WITH_CNPG" = "1" ]; then
  if kubectl get crd clusters.postgresql.cnpg.io >/dev/null 2>&1; then
    ok "CloudNativePG already installed"
  elif kubectl apply --server-side -f \
    "https://raw.githubusercontent.com/cloudnative-pg/cloudnative-pg/release-1.24/releases/cnpg-1.24.1.yaml" \
    >/dev/null 2>&1; then
    ok "CloudNativePG operator installed (the managed-database path is real)"
  else
    warn "CloudNativePG not installed; DATABASE_SERVICE deployments will not have an operator"
  fi
fi

# --- 6. build and load the application images ----------------------------------
info "Building the application images"
docker build -q -t aiinfra-backend:mvp -f "${ROOT_DIR}/backend/Dockerfile" "$ROOT_DIR" >/dev/null \
  || die "backend image build failed"
docker build -q -t aiinfra-frontend:mvp -f "${ROOT_DIR}/frontend/Dockerfile" "$ROOT_DIR" >/dev/null \
  || die "frontend image build failed"
ok "images built"

info "Loading images into kind (no registry needed)"
kind load docker-image aiinfra-backend:mvp aiinfra-frontend:mvp --name "$CLUSTER" >/dev/null \
  || die "could not load the images into kind"
ok "images loaded"

# --- 7. deploy the platform -----------------------------------------------------
info "Deploying the platform into the cluster"
kubectl apply -f "${ROOT_DIR}/deployments/mvp/kubernetes/platform/postgres.yaml" >/dev/null
kubectl apply -f "${ROOT_DIR}/deployments/mvp/kubernetes/platform/opa.yaml" >/dev/null
kubectl apply -f "${ROOT_DIR}/deployments/mvp/kubernetes/platform/backend.yaml" >/dev/null
kubectl apply -f "${ROOT_DIR}/deployments/mvp/kubernetes/platform/frontend.yaml" >/dev/null
kubectl apply -f "${ROOT_DIR}/deployments/mvp/kubernetes/monitoring/prometheus.yaml" >/dev/null

# Always restart the backend and frontend so a rebuilt image is picked up.
kubectl -n aiinfra rollout restart deployment/backend deployment/frontend >/dev/null 2>&1 || true

info "Waiting for the platform to become ready"
kubectl -n aiinfra rollout status statefulset/postgres --timeout=240s >/dev/null 2>&1 \
  || warn "postgres is slow to start"
kubectl -n aiinfra rollout status deployment/opa --timeout=180s >/dev/null 2>&1 \
  || warn "opa is slow to start"
kubectl -n aiinfra rollout status deployment/backend --timeout=300s \
  || die "the backend never became ready -- kubectl -n aiinfra logs deploy/backend"
kubectl -n aiinfra rollout status deployment/frontend --timeout=180s >/dev/null 2>&1 \
  || warn "the frontend is slow to start"

# --- 8. verify -------------------------------------------------------------------
wait_for_http "http://localhost:8000/healthz" 180 "backend (NodePort 30001)" \
  || die "the backend is not reachable on localhost:8000"
wait_for_http "http://localhost:3000/healthz" 120 "frontend (NodePort 30000)" \
  || warn "the frontend is not reachable on localhost:3000 yet"

CAPS_URL="http://localhost:8000/api/system/capabilities"
MODE="$(json_field "$CAPS_URL" "data.get('mode','?')")"
K8S="$(json_field "$CAPS_URL" "data.get('kubernetes','?')")"
FLUX="$(json_field "$CAPS_URL" "data.get('flux','?')")"
OC="$(json_field "$CAPS_URL" "data.get('opencenter','?')")"
GPU="$(json_field "$CAPS_URL" "data.get('kubernetes_gpu','?')")"
GPU_REASON="$(json_field "$CAPS_URL" "data.get('kubernetes_gpu_reason','')")"
HOST_GPU="$(json_field "$CAPS_URL" "data.get('host_gpu','none')")"

[ "$MODE" = "mvp" ] || warn "backend reports mode '${MODE}', expected 'mvp'"

say ""
rule
say ""
say "${C_BOLD}GPU NATIVE INFRA - LOCAL MVP${C_RESET}"
say ""
say "Mode:              MVP (INFRA_MODE=mvp)"
say "kind cluster:      ${CLUSTER} (${NODES} nodes)"
say "Kubernetes:        $(printf '%s' "$K8S" | tr '[:lower:]' '[:upper:]')"
say "Flux:              $(printf '%s' "$FLUX" | tr '[:lower:]' '[:upper:]')"
say "openCenter:        $(printf '%s' "$OC" | tr '[:lower:]' '[:upper:]')  (never labelled 'real')"
say "OpenStack:         SIMULATED"
say "Genestack:         MOCK"
say "Ceph:              SIMULATED (ceph-rbd StorageClass is local-path)"
say "Host GPU:          $(printf '%s' "$HOST_GPU" | tr '[:lower:]' '[:upper:]')"
say "GPU in cluster:    $(printf '%s' "$GPU" | tr '[:lower:]' '[:upper:]')"
[ -n "$GPU_REASON" ] && say "                   ${GPU_REASON}"
say ""
say "UI:                http://localhost:3000"
say "API docs:          http://localhost:8000/docs"
say "Prometheus:        http://localhost:9090"
say "Login:             admin / admin   (POC ONLY)"
say ""
say "kubectl:           export KUBECONFIG=${KUBECONFIG_PATH}"
say ""
rule
say "Self test: ./selftest-mvp.sh      Status: ./status.sh      Stop: ./stopmvp.sh"
