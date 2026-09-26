#!/usr/bin/env bash
#
# One-screen health report for the POC.

set -uo pipefail
source "$(dirname "$0")/scripts/lib.sh"

cd "$ROOT_DIR"
export ENV_FILE="${ROOT_DIR}/.env"
export COMPOSE_PROJECT="${POC_PROJECT}"
export COMPOSE_PROFILES_ARGS="--profile observability"

BACKEND_PORT="$(env_value "$ENV_FILE" BACKEND_PORT 8000)"
FRONTEND_PORT="$(env_value "$ENV_FILE" FRONTEND_PORT 3000)"
OPA_PORT="$(env_value "$ENV_FILE" OPA_PORT 8181)"
OPENCENTER_PORT="$(env_value "$ENV_FILE" OPENCENTER_PORT 8080)"
GRAFANA_PORT="$(env_value "$ENV_FILE" GRAFANA_PORT 3001)"

FAILURES=0
row() {
  local name="$1" status="$2"
  if [ "$status" = "HEALTHY" ] || [ "$status" = "LOADED" ]; then
    printf '%-22s %b%s%b\n' "$name" "$C_GREEN" "$status" "$C_RESET"
  elif [ "$status" = "SKIPPED" ] || [ "$status" = "DISABLED" ]; then
    printf '%-22s %b%s%b\n' "$name" "$C_DIM" "$status" "$C_RESET"
  else
    printf '%-22s %b%s%b\n' "$name" "$C_RED" "$status" "$C_RESET"
    FAILURES=$((FAILURES + 1))
  fi
}

container_state() {
  compose ps --format '{{.Service}} {{.State}}' 2>/dev/null | awk -v s="$1" '$1==s{print $2}'
}

printf '%-22s %s\n\n' "SERVICE" "STATUS"

[ "$(http_status "http://localhost:${FRONTEND_PORT}/healthz")" = "200" ] \
  && row "Frontend" "HEALTHY" || row "Frontend" "DOWN"

[ "$(http_status "http://localhost:${BACKEND_PORT}/healthz")" = "200" ] \
  && row "Backend" "HEALTHY" || row "Backend" "DOWN"

case "$(container_state postgres)" in
  running) row "PostgreSQL" "HEALTHY" ;;
  '') row "PostgreSQL" "NOT RUNNING" ;;
  *) row "PostgreSQL" "$(container_state postgres | tr '[:lower:]' '[:upper:]')" ;;
esac

case "$(container_state redis)" in
  running) row "Redis" "HEALTHY" ;;
  '') row "Redis" "NOT RUNNING" ;;
  *) row "Redis" "$(container_state redis | tr '[:lower:]' '[:upper:]')" ;;
esac

[ "$(http_status "http://localhost:${OPA_PORT}/health")" = "200" ] \
  && row "OPA" "HEALTHY" || row "OPA" "DOWN"

[ "$(http_status "http://localhost:${OPENCENTER_PORT}/api/v1/health")" = "200" ] \
  && row "Mock openCenter" "HEALTHY" || row "Mock openCenter" "DOWN"

if [ -n "$(container_state grafana)" ]; then
  [ "$(http_status "http://localhost:${GRAFANA_PORT}/api/health")" = "200" ] \
    && row "Grafana" "HEALTHY" || row "Grafana" "DOWN"
else
  row "Grafana" "DISABLED"
fi

printf '\n'

GPU_TOTAL="$(json_field "http://localhost:${BACKEND_PORT}/api/system/capabilities" "data.get('mode','')" )"
if [ -n "$GPU_TOTAL" ]; then
  row "Simulation inventory" "LOADED"
else
  row "Simulation inventory" "NOT LOADED"
fi

POLICY="$(json_field "http://localhost:${BACKEND_PORT}/api/system/capabilities" "data.get('policy_engine','')")"
if [ -n "$POLICY" ]; then
  printf '%-22s %b%s%b (%s)\n' "Policies" "$C_GREEN" "LOADED" "$C_RESET" "$POLICY"
else
  row "Policies" "NOT LOADED"
fi

SCENARIOS="$(curl -fsS --max-time 5 "http://localhost:${BACKEND_PORT}/api/scenarios" \
  -H "Authorization: Bearer $(
    curl -fsS --max-time 5 -X POST "http://localhost:${BACKEND_PORT}/api/auth/login" \
      -H 'Content-Type: application/json' \
      -d "$(login_payload)" 2>/dev/null |
      python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])' 2>/dev/null
  )" 2>/dev/null | python3 -c 'import json,sys; print(len(json.load(sys.stdin)))' 2>/dev/null || echo 0)"
if [ "${SCENARIOS:-0}" -gt 0 ] 2>/dev/null; then
  printf '%-22s %b%s%b (%s)\n' "Demo scenarios" "$C_GREEN" "LOADED" "$C_RESET" "$SCENARIOS"
else
  row "Demo scenarios" "NOT LOADED"
fi

printf '\n'
if [ "$FAILURES" -eq 0 ]; then
  ok "everything is healthy -- open $(public_url "${FRONTEND_PORT}")"
  exit 0
fi

fail "${FAILURES} component(s) unhealthy"
say ""
say "Troubleshooting:"
say "  ./logs.sh backend          # application logs"
say "  ./logs.sh                  # backend + frontend"
say "  docker compose -p ${POC_PROJECT} ps"
say "  docs/troubleshooting.md    # port conflicts, WSL, Docker Desktop"
exit 1
