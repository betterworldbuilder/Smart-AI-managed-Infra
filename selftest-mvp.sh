#!/usr/bin/env bash
#
# Sixteen checks against the REAL kind MVP: the cluster, Flux, the platform,
# and a full Copilot -> approval -> real Kubernetes pod round trip.

set -uo pipefail
source "$(dirname "$0")/scripts/lib.sh"

cd "$ROOT_DIR"
MVP_ENV="${ROOT_DIR}/deployments/mvp/.env.mvp"
CLUSTER="$(env_value "$MVP_ENV" KIND_CLUSTER_NAME gpu-native-mvp)"
export KUBECONFIG="${ROOT_DIR}/deployments/mvp/kubeconfig"
API="http://localhost:8000/api"

PASSED=0
FAILED=0
TOKEN=""

check() {
  local label="$1" result="$2" detail="${3:-}"
  if [ "$result" = "pass" ]; then
    PASSED=$((PASSED + 1))
    printf ' %b✓%b %-36s %s\n' "$C_GREEN" "$C_RESET" "$label" "$detail"
  else
    FAILED=$((FAILED + 1))
    printf ' %b✗%b %-36s %s\n' "$C_RED" "$C_RESET" "$label" "$detail"
  fi
}

api_get() { curl -fsS --max-time 15 -H "Authorization: Bearer ${TOKEN}" "${API}$1" 2>/dev/null; }
api_post() {
  curl -fsS --max-time 60 -X POST -H "Authorization: Bearer ${TOKEN}" \
    -H 'Content-Type: application/json' -d "${2:-{\}}" "${API}$1" 2>/dev/null
}

rule
say "${C_BOLD}GPU Native Infra -- MVP self test (real kind cluster)${C_RESET}"
rule

# 1. Docker --------------------------------------------------------------------
if docker info >/dev/null 2>&1; then
  check "1. Docker" pass "$(docker version --format '{{.Server.Version}}' 2>/dev/null)"
else
  check "1. Docker" fail "daemon unreachable"
fi

# 2. kind cluster ---------------------------------------------------------------
if kind get clusters 2>/dev/null | grep -qx "$CLUSTER"; then
  check "2. kind cluster" pass "$CLUSTER"
else
  check "2. kind cluster" fail "cluster '${CLUSTER}' not found -- run ./startmvp.sh"
fi

# 3. Kubernetes API ---------------------------------------------------------------
NODE_COUNT="$(kubectl get nodes --no-headers 2>/dev/null | wc -l | tr -d ' ')"
if [ "${NODE_COUNT:-0}" -ge 1 ]; then
  check "3. Kubernetes API" pass "${NODE_COUNT} node(s) Ready"
else
  check "3. Kubernetes API" fail "no nodes"
fi

# 4. Flux ---------------------------------------------------------------------------
if kubectl get crd kustomizations.kustomize.toolkit.fluxcd.io >/dev/null 2>&1; then
  FLUX_PODS="$(kubectl -n flux-system get pods --no-headers 2>/dev/null | grep -c Running || echo 0)"
  check "4. FluxCD" pass "${FLUX_PODS} controller pod(s) running"
else
  check "4. FluxCD" fail "Flux CRDs not installed (compat mode still works)"
fi

# 5. backend ---------------------------------------------------------------------------
if [ "$(http_status "${API%/api}/healthz")" = "200" ]; then
  check "5. backend" pass "http://localhost:8000"
else
  check "5. backend" fail "not reachable on localhost:8000"
fi

# 6. frontend ---------------------------------------------------------------------------
if [ "$(http_status "http://localhost:3000/healthz")" = "200" ]; then
  check "6. frontend" pass "http://localhost:3000"
else
  check "6. frontend" fail "not reachable on localhost:3000"
fi

# 7. PostgreSQL ---------------------------------------------------------------------------
if kubectl -n aiinfra exec statefulset/postgres -- pg_isready -U gpuinfra -d gpuinfra >/dev/null 2>&1; then
  check "7. PostgreSQL" pass "accepting connections"
else
  check "7. PostgreSQL" fail "pg_isready failed"
fi

# 8. OPA ---------------------------------------------------------------------------
if kubectl -n aiinfra get deploy opa -o jsonpath='{.status.readyReplicas}' 2>/dev/null | grep -q 1; then
  check "8. OPA" pass "ready in-cluster"
else
  check "8. OPA" fail "not ready"
fi

# 9. real node inventory ----------------------------------------------------------------
TOKEN="$(curl -fsS --max-time 10 -X POST "${API}/auth/login" \
  -H 'Content-Type: application/json' -d '{"username":"admin","password":"admin"}' \
  2>/dev/null | pyjson "data['access_token']")"
INVENTORY_NODES="$(api_get /inventory/kubernetes | pyjson "len(data['capacity']['nodes'])")"
SIMULATED="$(api_get /inventory/kubernetes | pyjson "data['simulated']")"
if [ "${INVENTORY_NODES:-0}" = "${NODE_COUNT}" ] && [ "$SIMULATED" = "False" ]; then
  check "9. real node inventory" pass "${INVENTORY_NODES} nodes read from the cluster API"
else
  check "9. real node inventory" fail "inventory nodes=${INVENTORY_NODES:-?} simulated=${SIMULATED:-?}"
fi

# 10. Copilot ---------------------------------------------------------------------------
CHAT="$(api_post /copilot/messages '{"message":"Deploy an internal web API for the engineering team, about 30 concurrent requests."}')"
CONVERSATION="$(printf '%s' "$CHAT" | pyjson "data['conversation']['id']")"
if [ -n "$CONVERSATION" ]; then
  check "10. Copilot" pass "conversation ${CONVERSATION}"
else
  check "10. Copilot" fail "no conversation"
fi

# 11. recommendation ---------------------------------------------------------------------
ANSWER="$(api_post "/copilot/conversations/${CONVERSATION}/answers" \
  '{"answers":{"environment":"dev","sensitive_data":"internal"}}')"
RECOMMENDATION="$(printf '%s' "$ANSWER" | pyjson "data['recommendation']['id']")"
OPTION="$(printf '%s' "$ANSWER" | pyjson "data['recommendation']['options'][0]['option']")"
RUNTIME="$(printf '%s' "$ANSWER" | pyjson "data['recommendation']['options'][0]['runtime']")"
if [ "$RUNTIME" = "KUBERNETES_CPU" ]; then
  check "11. recommendation engine" pass "recommended ${RUNTIME}"
else
  check "11. recommendation engine" fail "recommended '${RUNTIME:-nothing}'"
fi

# 12. human approval -----------------------------------------------------------------------
DEPLOYMENT="$(api_post /deployments \
  "{\"recommendation_id\":\"${RECOMMENDATION}\",\"option\":\"${OPTION}\"}" | pyjson "data['id']")"
STATE="$(api_post "/deployments/${DEPLOYMENT}/approve" '{"note":"mvp selftest"}' | pyjson "data['state']")"
if [ "$STATE" = "WAITING_FOR_FINAL_APPROVAL" ]; then
  check "12. human approval" pass "gate 1 passed, plan generated"
else
  check "12. human approval" fail "state=${STATE:-none}"
fi

# 13. manifest generation --------------------------------------------------------------------
ARTIFACTS="$(api_get "/deployments/${DEPLOYMENT}/artifacts" | pyjson "len(data)")"
HAS_DEPLOYMENT_YAML="$(api_get "/deployments/${DEPLOYMENT}/artifacts" |
  pyjson "any(a['path'].endswith('deployment.yaml') for a in data)")"
if [ "$HAS_DEPLOYMENT_YAML" = "True" ]; then
  check "13. manifest generation" pass "${ARTIFACTS} files generated"
else
  check "13. manifest generation" fail "no deployment.yaml generated"
fi

# 14. apply through the GitOps tree ------------------------------------------------------------
APPLIED="$(api_post "/deployments/${DEPLOYMENT}/apply" '{"note":"mvp selftest"}' | pyjson "data['state']")"
if [ "$APPLIED" = "DEPLOYING" ]; then
  check "14. GitOps apply" pass "pipeline started"
else
  check "14. GitOps apply" fail "state=${APPLIED:-none}"
fi

# 15. a real pod reaches Running -----------------------------------------------------------------
STATE=""
for _ in $(seq 1 90); do
  STATE="$(api_get "/deployments/${DEPLOYMENT}" | pyjson "data['state']")"
  [ "$STATE" = "RUNNING" ] && break
  [ "$STATE" = "FAILED" ] && break
  sleep 2
done
NAME="$(api_get "/deployments/${DEPLOYMENT}" | pyjson "data['name']")"
PODS="$(kubectl -n "ai-${NAME}" get pods --no-headers 2>/dev/null | grep -c Running || echo 0)"
if [ "$STATE" = "RUNNING" ] && [ "${PODS:-0}" -ge 1 ]; then
  NODE="$(kubectl -n "ai-${NAME}" get pods -o jsonpath='{.items[0].spec.nodeName}' 2>/dev/null)"
  check "15. real pod Running" pass "${PODS} pod(s) in ai-${NAME} on ${NODE}"
else
  REASON="$(api_get "/deployments/${DEPLOYMENT}" | pyjson "data.get('failure_reason') or ''")"
  check "15. real pod Running" fail "state=${STATE:-?} pods=${PODS:-0} ${REASON}"
fi

# 16. real Kubernetes metrics ------------------------------------------------------------------
LIVE_PODS="$(api_get "/deployments/${DEPLOYMENT}/status" | pyjson "len((data.get('live') or {}).get('pods', []))")"
CLUSTER_CPU="$(api_get /metrics/summary | pyjson "data['cpu_utilization_pct']")"
if [ "${LIVE_PODS:-0}" -ge 1 ] && [ -n "$CLUSTER_CPU" ]; then
  check "16. real Kubernetes metrics" pass "${LIVE_PODS} pod(s) reported, cluster CPU ${CLUSTER_CPU}%"
else
  check "16. real Kubernetes metrics" fail "live pods=${LIVE_PODS:-0}"
fi

rule
TOTAL=$((PASSED + FAILED))
if [ "$FAILED" -eq 0 ]; then
  say "${C_GREEN}${PASSED} / ${TOTAL} tests passed${C_RESET}"
  say ""
  say "${C_BOLD}MVP READY${C_RESET}"
  rule
  exit 0
fi
say "${C_RED}${PASSED} / ${TOTAL} tests passed, ${FAILED} failed${C_RESET}"
say ""
say "Diagnostics:"
say "  kubectl --kubeconfig ${KUBECONFIG} -n aiinfra get pods"
say "  kubectl --kubeconfig ${KUBECONFIG} -n aiinfra logs deploy/backend --tail=100"
say "  docs/troubleshooting.md"
rule
exit 1
