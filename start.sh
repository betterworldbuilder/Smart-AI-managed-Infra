#!/usr/bin/env bash
#
# Start the simulated POC and wait until it is genuinely healthy.

set -uo pipefail
source "$(dirname "$0")/scripts/lib.sh"

NO_OBSERVABILITY=0
for arg in "$@"; do
  case "$arg" in
    --no-observability) NO_OBSERVABILITY=1 ;;
    -h | --help)
      say "Usage: ./start.sh [--no-observability]"
      exit 0
      ;;
  esac
done

cd "$ROOT_DIR"
require_docker

export ENV_FILE="${ROOT_DIR}/.env"
export COMPOSE_PROJECT="${POC_PROJECT}"
[ "$NO_OBSERVABILITY" = "0" ] && export COMPOSE_PROFILES_ARGS="--profile observability"

if [ ! -f "$ENV_FILE" ]; then
  warn ".env is missing -- creating it from .env.example"
  ensure_env_file "$ENV_FILE"
fi

BACKEND_PORT="$(env_value "$ENV_FILE" BACKEND_PORT 8000)"
FRONTEND_PORT="$(env_value "$ENV_FILE" FRONTEND_PORT 3000)"
GRAFANA_PORT="$(env_value "$ENV_FILE" GRAFANA_PORT 3001)"

info "Starting the simulation stack"
compose up -d || die "docker compose up failed -- see ./logs.sh"

info "Waiting for dependencies"
# PostgreSQL and Redis are gated by compose health checks; confirm anyway so a
# failure is reported here rather than as a confusing backend error.
for service in postgres redis; do
  state="$(compose ps --format '{{.Service}} {{.State}}' 2>/dev/null | awk -v s="$service" '$1==s{print $2}')"
  if [ "$state" = "running" ]; then ok "$service running"; else warn "$service state: ${state:-unknown}"; fi
done

wait_for_http "http://localhost:${BACKEND_PORT}/healthz" 180 "backend" \
  || die "backend unhealthy -- ./logs.sh backend"
wait_for_http "http://localhost:${FRONTEND_PORT}/healthz" 120 "frontend" \
  || die "frontend unhealthy -- ./logs.sh frontend"

# The API must be able to answer a real question, not just return 200 on /healthz.
if [ "$(http_status "http://localhost:${BACKEND_PORT}/api/system/capabilities")" != "200" ]; then
  die "the backend is up but the API is not answering -- ./logs.sh backend"
fi

MODE="$(json_field "http://localhost:${BACKEND_PORT}/api/system/capabilities" "data.get('mode_label','?')")"

say ""
rule
say "${C_BOLD}GPU Native Infra POC is ready.${C_RESET}"
rule
say ""
say "Open:"
say "http://localhost:${FRONTEND_PORT}"
say ""
say "Mode:        ${MODE}"
say "API docs:    http://localhost:${BACKEND_PORT}/docs"
[ "$NO_OBSERVABILITY" = "0" ] && say "Grafana:     http://localhost:${GRAFANA_PORT}"
say "Login:       admin / admin   (POC ONLY)"
say ""
say "Try: \"Deploy a private Llama service for 100 users\""
rule
