#!/usr/bin/env bash
#
# Start the fully simulated POC (Docker Compose, project gpuinfra-poc).
#
# This is the demo mode: the Copilot, Governor, policy engine and approval
# workflow are REAL; everything below them is simulated.

set -uo pipefail
source "$(dirname "$0")/scripts/lib.sh"

cd "$ROOT_DIR"

NO_OBSERVABILITY=0
for arg in "$@"; do
  case "$arg" in
    --no-observability) NO_OBSERVABILITY=1 ;;
    -y | --yes) export ASSUME_YES=1 ;;
    -h | --help)
      say "Usage: ./startpoc.sh [--no-observability] [-y]"
      exit 0
      ;;
  esac
done

# 1. Docker -----------------------------------------------------------------
info "Checking Docker"
require_docker
ok "docker ready"

# 2 + 3. POC environment ------------------------------------------------------
POC_ENV="${ROOT_DIR}/deployments/poc/.env.poc"
ensure_env_file "$POC_ENV"

force_env() {
  local key="$1" value="$2" tmp
  tmp="$(mktemp)"
  if grep -qE "^${key}=" "$POC_ENV"; then
    sed "s|^${key}=.*|${key}=${value}|" "$POC_ENV" >"$tmp" && mv "$tmp" "$POC_ENV"
  else
    printf '%s=%s\n' "$key" "$value" >>"$POC_ENV"
  fi
}

force_env INFRA_MODE simulation
force_env SIMULATION_MODE true
force_env LLM_PROVIDER "$(env_value "$POC_ENV" LLM_PROVIDER mock)"
force_env OPEN_CENTER_MODE mock
force_env OPENSTACK_MODE mock
force_env CEPH_MODE mock
force_env GENESTACK_MODE mock
# No login in the POC. Opt back in with: AUTH_ENABLED=true ./startpoc.sh
force_env AUTH_ENABLED "${AUTH_ENABLED:-false}"
ok "POC environment pinned to simulation"

export ENV_FILE="$POC_ENV"
resolve_public_host
harden_public_credentials "$POC_ENV"

export ENV_FILE="$POC_ENV"
export COMPOSE_PROJECT="${POC_PROJECT}"
[ "$NO_OBSERVABILITY" = "0" ] && export COMPOSE_PROFILES_ARGS="--profile observability"

FRONTEND_PORT="$(env_value "$POC_ENV" FRONTEND_PORT 3000)"
BACKEND_PORT="$(env_value "$POC_ENV" BACKEND_PORT 8000)"
GRAFANA_PORT="$(env_value "$POC_ENV" GRAFANA_PORT 3001)"

# 4. Start --------------------------------------------------------------------
info "Starting the POC stack (project ${POC_PROJECT})"
compose up -d --build || die "docker compose up failed -- ./logs.sh"

# 5 + 6 + 7. The backend initialises the database, loads the simulated
# inventory and the demo scenarios on start-up; OPA loads policies/rego.
info "Waiting for the control plane"
wait_for_http "http://localhost:${BACKEND_PORT}/healthz" 180 "backend" \
  || die "backend unhealthy -- ./logs.sh backend"
wait_for_http "http://localhost:${FRONTEND_PORT}/healthz" 120 "frontend" \
  || die "frontend unhealthy -- ./logs.sh frontend"

# 9. Health -------------------------------------------------------------------
CAPS_URL="http://localhost:${BACKEND_PORT}/api/system/capabilities"
MODE="$(json_field "$CAPS_URL" "data.get('mode','?')")"
LLM="$(json_field "$CAPS_URL" "data.get('llm_provider','?')")"
OC="$(json_field "$CAPS_URL" "data.get('opencenter','?')")"
K8S="$(json_field "$CAPS_URL" "data.get('kubernetes','?')")"
OS_MODE="$(json_field "$CAPS_URL" "data.get('openstack','?')")"
GPU="$(json_field "$CAPS_URL" "data.get('kubernetes_gpu','?')")"

[ "$MODE" = "simulation" ] || warn "expected simulation mode, backend reports '${MODE}'"

# 10. Banner --------------------------------------------------------------------
say ""
rule
say ""
say "${C_BOLD}GPU NATIVE INFRA - MOCK POC${C_RESET}"
say ""
say "Mode:"
say "SIMULATION"
say ""
say "AI Copilot:"
say "REAL"
say ""
say "AI Governor:"
say "REAL"
say ""
say "Infrastructure:"
say "SIMULATED"
say ""
say "openCenter:"
say "$(printf '%s' "$OC" | tr '[:lower:]' '[:upper:]')"
say ""
say "Kubernetes:"
say "$(printf '%s' "$K8S" | tr '[:lower:]' '[:upper:]')"
say ""
say "OpenStack:"
say "$(printf '%s' "$OS_MODE" | tr '[:lower:]' '[:upper:]')"
say ""
say "GPU:"
say "SIMULATED (${GPU})"
say ""
say "AI provider:"
say "$(printf '%s' "$LLM" | tr '[:lower:]' '[:upper:]')"
say ""
say "UI:"
say "$(public_url "${FRONTEND_PORT}")"
say ""
say "API docs:  $(public_url "${BACKEND_PORT}" /docs)"
[ "$NO_OBSERVABILITY" = "0" ] && say "Grafana:   $(public_url "${GRAFANA_PORT}")"
print_credentials "$POC_ENV"
print_public_access_hint "${FRONTEND_PORT}"
say ""
rule
say "Self test: ./selftest-poc.sh      Stop: ./stoppoc.sh"
