#!/usr/bin/env bash
#
# What is running right now: the POC, the MVP, or neither.

set -uo pipefail
source "$(dirname "$0")/scripts/lib.sh"

cd "$ROOT_DIR"
MVP_ENV="${ROOT_DIR}/deployments/mvp/.env.mvp"
CLUSTER="$(env_value "$MVP_ENV" KIND_CLUSTER_NAME gpu-native-mvp)"

field() { printf '%-20s %s\n' "$1:" "$2"; }
upper() { printf '%s' "${1:-unknown}" | tr '[:lower:]' '[:upper:]'; }

say "${C_BOLD}GPU Native Infra${C_RESET}"
say ""

# --- POC ---------------------------------------------------------------------
POC_RUNNING="STOPPED"
POC_PORT="$(env_value "${ROOT_DIR}/deployments/poc/.env.poc" BACKEND_PORT 8000)"
if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  RUNNING="$(docker ps --filter "label=com.docker.compose.project=${POC_PROJECT}" -q | wc -l | tr -d ' ')"
  [ "${RUNNING:-0}" -gt 0 ] && POC_RUNNING="RUNNING (${RUNNING} containers)"
fi
field "POC" "$POC_RUNNING"

# --- MVP ----------------------------------------------------------------------
MVP_RUNNING="STOPPED"
KIND_STATE="ABSENT"
if command -v kind >/dev/null 2>&1 && kind get clusters 2>/dev/null | grep -qx "$CLUSTER"; then
  KIND_STATE="READY"
  export KUBECONFIG="${ROOT_DIR}/deployments/mvp/kubeconfig"
  if kubectl get ns aiinfra >/dev/null 2>&1; then
    READY="$(kubectl -n aiinfra get deploy backend -o jsonpath='{.status.readyReplicas}' 2>/dev/null || echo 0)"
    [ "${READY:-0}" -gt 0 ] && MVP_RUNNING="RUNNING"
  fi
fi
field "MVP" "$MVP_RUNNING"
say ""
field "kind" "$CLUSTER"
field "" "$KIND_STATE"
say ""

# --- capabilities from whichever backend answers ---------------------------------
API=""
for port in 8000 "$POC_PORT"; do
  if [ "$(http_status "http://localhost:${port}/api/system/capabilities")" = "200" ]; then
    API="http://localhost:${port}/api"
    break
  fi
done

if [ -z "$API" ]; then
  warn "no backend is answering; start one with ./startpoc.sh or ./startmvp.sh"
  exit 0
fi

CAPS="$(curl -fsS --max-time 5 "${API}/system/capabilities" 2>/dev/null)"
get() { printf '%s' "$CAPS" | pyjson "data.get('$1','unknown')"; }

field "Backend" "$API"
field "Mode" "$(upper "$(get mode)")"
field "Kubernetes" "$(upper "$(get kubernetes)")"
field "Flux" "$(upper "$(get flux)")"
field "AI Copilot" "HEALTHY"
field "Governor" "HEALTHY"
field "Policy engine" "$(upper "$(get policy_engine)")"
field "openCenter" "$(upper "$(get opencenter)")"
field "Deployment engine" "$(get deployment_engine)"
field "Genestack" "$(upper "$(get genestack)")"
field "OpenStack" "$(upper "$(get openstack)")"
field "Ceph" "$(upper "$(get ceph)")"
field "AI provider" "$(upper "$(get llm_provider)")"
say ""

HOST_GPU="$(get host_gpu)"
MODELS="$(printf '%s' "$CAPS" | pyjson "', '.join(data.get('host_gpu_models') or []) or 'none detected'")"
field "Host GPU" "$(upper "$HOST_GPU") (${MODELS})"
field "GPU visible to kind" "$(upper "$(get kubernetes_gpu)")"
REASON="$(get kubernetes_gpu_reason)"
[ -n "$REASON" ] && printf '%-20s %s\n' "" "$REASON"

# --- database and store ------------------------------------------------------------
HEALTH="$(curl -fsS --max-time 5 "${API}/health" 2>/dev/null)"
say ""
field "Store" "$(printf '%s' "$HEALTH" | pyjson "data.get('store','?')")"
field "Redis" "$(printf '%s' "$HEALTH" | pyjson "'connected' if data.get('redis',{}).get('reachable') else 'not connected'")"
