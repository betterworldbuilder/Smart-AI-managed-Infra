#!/usr/bin/env bash
#
# End-to-end self test of the running POC: ten checks, from "is the backend up"
# to "did a simulated deployment actually reach RUNNING".
#
#   ./selftest.sh

set -uo pipefail
source "$(dirname "$0")/scripts/lib.sh"

cd "$ROOT_DIR"
export ENV_FILE="${ROOT_DIR}/.env"
export COMPOSE_PROJECT="${POC_PROJECT}"

BACKEND_PORT="$(env_value "$ENV_FILE" BACKEND_PORT 8000)"
OPA_PORT="$(env_value "$ENV_FILE" OPA_PORT 8181)"
API="http://localhost:${BACKEND_PORT}/api"

PASSED=0
FAILED=0
TOKEN=""

check() {
  local label="$1" result="$2" detail="${3:-}"
  if [ "$result" = "pass" ]; then
    PASSED=$((PASSED + 1))
    printf ' %b✓%b %-34s %s\n' "$C_GREEN" "$C_RESET" "$label" "$detail"
  else
    FAILED=$((FAILED + 1))
    printf ' %b✗%b %-34s %s\n' "$C_RED" "$C_RESET" "$label" "$detail"
  fi
}

api_get() {
  curl -fsS --max-time 10 -H "Authorization: Bearer ${TOKEN}" "${API}$1" 2>/dev/null
}

api_post() {
  curl -fsS --max-time 30 -X POST -H "Authorization: Bearer ${TOKEN}" \
    -H 'Content-Type: application/json' -d "${2:-{\}}" "${API}$1" 2>/dev/null
}

rule
say "${C_BOLD}GPU Native Infra POC -- self test${C_RESET}"
rule

# 1. Backend health ---------------------------------------------------------
HEALTH="$(curl -fsS --max-time 10 "${API}/health" 2>/dev/null || true)"
if [ -n "$HEALTH" ]; then
  check "1. backend health" pass "$(printf '%s' "$HEALTH" | pyjson "data.get('mode','?')")"
else
  check "1. backend health" fail "no response from ${API}/health"
fi

# 2. Database ----------------------------------------------------------------
STORE="$(printf '%s' "$HEALTH" | pyjson "data.get('store','')")"
case "$STORE" in
  SqlStore) check "2. PostgreSQL connection" pass "SqlStore" ;;
  InMemoryStore) check "2. PostgreSQL connection" pass "in-memory store (DATABASE_URL unset)" ;;
  *) check "2. PostgreSQL connection" fail "unknown store '${STORE}'" ;;
esac

# 3. Redis --------------------------------------------------------------------
REDIS="$(printf '%s' "$HEALTH" | pyjson "data.get('redis',{}).get('reachable')")"
if [ "$REDIS" = "True" ]; then
  check "3. Redis" pass "connected"
elif [ "$(printf '%s' "$HEALTH" | pyjson "data.get('redis',{}).get('configured')")" = "False" ]; then
  check "3. Redis" pass "not configured (optional)"
else
  check "3. Redis" fail "configured but unreachable"
fi

# 4. OPA ----------------------------------------------------------------------
if [ "$(http_status "http://localhost:${OPA_PORT}/health")" = "200" ]; then
  ENGINE="$(curl -fsS "${API}/system/capabilities" 2>/dev/null | pyjson "data.get('policy_engine','')")"
  check "4. OPA policy engine" pass "reachable, engine=${ENGINE}"
else
  check "4. OPA policy engine" fail "http://localhost:${OPA_PORT}/health did not answer"
fi

# Authenticate for the remaining checks.
TOKEN="$(curl -fsS --max-time 10 -X POST "${API}/auth/login" \
  -H 'Content-Type: application/json' \
  -d "$(login_payload)" 2>/dev/null | pyjson "data['access_token']")"
if [ -z "$TOKEN" ]; then
  check "   authentication" fail "could not obtain an API token"
fi

# 5. Inventory ----------------------------------------------------------------
GPUS="$(api_get /inventory/gpus | pyjson "data.get('total',0)")"
if [ "${GPUS:-0}" -ge 1 ] 2>/dev/null; then
  check "5. resource inventory" pass "${GPUS} GPUs known"
else
  check "5. resource inventory" fail "no GPUs in inventory"
fi

# 6. Copilot -------------------------------------------------------------------
CHAT="$(api_post /copilot/messages \
  '{"message":"Deploy a private Llama inference service for 100 employees."}')"
CONVERSATION="$(printf '%s' "$CHAT" | pyjson "data['conversation']['id']")"
WORKLOAD="$(printf '%s' "$CHAT" | pyjson "data['conversation']['intent']['workload_type']")"
if [ -n "$CONVERSATION" ] && [ "$WORKLOAD" = "llm-inference" ]; then
  check "6. Copilot endpoint" pass "classified as ${WORKLOAD}"
else
  check "6. Copilot endpoint" fail "unexpected response"
fi

# 7. Recommendation engine -------------------------------------------------------
ANSWER="$(api_post "/copilot/conversations/${CONVERSATION}/answers" \
  '{"answers":{"concurrent_users":20,"environment":"prod"}}')"
RECOMMENDATION="$(printf '%s' "$ANSWER" | pyjson "data['recommendation']['id']")"
TOP_OPTION="$(printf '%s' "$ANSWER" | pyjson "data['recommendation']['options'][0]['option']")"
TOP_RUNTIME="$(printf '%s' "$ANSWER" | pyjson "data['recommendation']['options'][0]['runtime']")"
if [ -n "$RECOMMENDATION" ] && [ "$TOP_RUNTIME" = "KUBERNETES_GPU" ]; then
  check "7. recommendation engine" pass "recommended ${TOP_RUNTIME}"
else
  check "7. recommendation engine" fail "got '${TOP_RUNTIME:-nothing}'"
fi

# 8. Approval --------------------------------------------------------------------
DEPLOYMENT="$(api_post /deployments \
  "{\"recommendation_id\":\"${RECOMMENDATION}\",\"option\":\"${TOP_OPTION}\"}" | pyjson "data['id']")"
APPROVED="$(api_post "/deployments/${DEPLOYMENT}/approve" '{"note":"selftest"}' | pyjson "data['state']")"
if [ "$APPROVED" = "WAITING_FOR_FINAL_APPROVAL" ]; then
  check "8. approval endpoint" pass "plan generated, awaiting the second gate"
else
  check "8. approval endpoint" fail "state after approval: ${APPROVED:-none}"
fi

# 9. openCenter deployment --------------------------------------------------------
APPLIED="$(api_post "/deployments/${DEPLOYMENT}/apply" '{"note":"selftest"}' | pyjson "data['state']")"
if [ "$APPLIED" = "DEPLOYING" ]; then
  check "9. mock openCenter deployment" pass "pipeline started"
else
  check "9. mock openCenter deployment" fail "state after apply: ${APPLIED:-none}"
fi

# 10. Reaches RUNNING ---------------------------------------------------------------
STATE=""
for _ in $(seq 1 60); do
  STATE="$(api_get "/deployments/${DEPLOYMENT}" | pyjson "data['state']")"
  [ "$STATE" = "RUNNING" ] && break
  [ "$STATE" = "FAILED" ] && break
  sleep 2
done
if [ "$STATE" = "RUNNING" ]; then
  NODE="$(api_get "/deployments/${DEPLOYMENT}" | pyjson "(data.get('placement') or {}).get('node','?')")"
  check "10. deployment reaches RUNNING" pass "placed on ${NODE} by the platform scheduler"
else
  REASON="$(api_get "/deployments/${DEPLOYMENT}" | pyjson "data.get('failure_reason') or ''")"
  check "10. deployment reaches RUNNING" fail "state=${STATE:-unknown} ${REASON}"
fi

rule
TOTAL=$((PASSED + FAILED))
if [ "$FAILED" -eq 0 ]; then
  say "${C_GREEN}${PASSED}/${TOTAL} tests passed${C_RESET}"
  say ""
  say "${C_BOLD}ALL TESTS PASSED${C_RESET}"
  say "GPU Native Infra POC is working correctly."
  rule
  exit 0
fi

say "${C_RED}${PASSED}/${TOTAL} tests passed, ${FAILED} failed${C_RESET}"
say ""
say "Troubleshooting: ./logs.sh backend   |   docs/troubleshooting.md"
rule
exit 1
