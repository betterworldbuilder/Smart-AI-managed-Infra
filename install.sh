#!/usr/bin/env bash
#
# One-command installation for the simulated POC.
#
#   ./install.sh && ./start.sh
#
# Needs Docker. Nothing else: no Python, Node, PostgreSQL, GPU, Kubernetes,
# OpenStack, Ceph, openCenter or external LLM.

set -uo pipefail
source "$(dirname "$0")/scripts/lib.sh"

NO_OBSERVABILITY=0
for arg in "$@"; do
  case "$arg" in
    --no-observability) NO_OBSERVABILITY=1 ;;
    -y | --yes) export ASSUME_YES=1 ;;
    -h | --help)
      say "Usage: ./install.sh [--no-observability] [-y]"
      exit 0
      ;;
  esac
done

cd "$ROOT_DIR"
rule
say "${C_BOLD}Installing the GPU Native Infra POC${C_RESET}"
rule

# 1 + 2. Docker and Compose -------------------------------------------------
info "Checking prerequisites"
require_docker
ok "docker $(docker version --format '{{.Server.Version}}' 2>/dev/null || echo present)"
ok "$(docker compose version | head -n1)"

# 3. .env -------------------------------------------------------------------
info "Preparing configuration"
ensure_env_file "${ROOT_DIR}/.env"
ensure_env_file "${ROOT_DIR}/deployments/poc/.env.poc"

# On a public host (EC2), never ship admin/admin to the internet.
resolve_public_host
harden_public_credentials "${ROOT_DIR}/.env"
harden_public_credentials "${ROOT_DIR}/deployments/poc/.env.poc"
ok "public address: $(detect_public_host)"

# 4. Directories ------------------------------------------------------------
mkdir -p "${ROOT_DIR}/deployments/mvp/flux/workloads/generated" \
  "${ROOT_DIR}/deployments/poc/seed" \
  "${ROOT_DIR}/config"
ok "working directories ready"

ports_in_use \
  "$(env_value "${ROOT_DIR}/.env" FRONTEND_PORT 3000)" \
  "$(env_value "${ROOT_DIR}/.env" BACKEND_PORT 8000)" \
  "$(env_value "${ROOT_DIR}/.env" GRAFANA_PORT 3001)"

# 5. Build ------------------------------------------------------------------
export ENV_FILE="${ROOT_DIR}/.env"
export COMPOSE_PROJECT="${POC_PROJECT}"
if [ "$NO_OBSERVABILITY" = "0" ]; then
  export COMPOSE_PROFILES_ARGS="--profile observability"
fi

info "Building containers (first run downloads base images -- a few minutes)"
if ! compose build; then
  die "the image build failed -- see the output above, and docs/troubleshooting.md"
fi
ok "images built"

# 6 + 7 + 8 + 9. Start the stack, which initialises PostgreSQL, loads the
# simulated inventory, the demo scenarios and the OPA policies.
info "Starting services"
compose up -d || die "docker compose up failed"

BACKEND_PORT="$(env_value "${ROOT_DIR}/.env" BACKEND_PORT 8000)"
FRONTEND_PORT="$(env_value "${ROOT_DIR}/.env" FRONTEND_PORT 3000)"
GRAFANA_PORT="$(env_value "${ROOT_DIR}/.env" GRAFANA_PORT 3001)"

wait_for_http "http://localhost:${BACKEND_PORT}/healthz" 180 "backend" \
  || die "the backend never became healthy -- try: ./logs.sh backend"
wait_for_http "http://localhost:${FRONTEND_PORT}/healthz" 120 "frontend" \
  || warn "the frontend is slow to start; check ./logs.sh frontend"

# 10. Verify ----------------------------------------------------------------
info "Verifying the installation"
GPUS="$(json_field "http://localhost:${BACKEND_PORT}/api/system/capabilities" \
  "data.get('mode_label','?')")"
ok "backend reports mode: ${GPUS:-unknown}"

if [ -x "${ROOT_DIR}/health.sh" ]; then
  "${ROOT_DIR}/health.sh" || warn "some components are not healthy yet"
fi

# 11. Done ------------------------------------------------------------------
say ""
rule
say "${C_BOLD}GPU Native Infra POC installed successfully${C_RESET}"
rule
say ""
say "Frontend:"
say "$(public_url "${FRONTEND_PORT}")"
say ""
say "Backend API:"
say "$(public_url "${BACKEND_PORT}")"
say ""
say "API Docs:"
say "$(public_url "${BACKEND_PORT}" /docs)"
say ""
if [ "$NO_OBSERVABILITY" = "0" ]; then
  say "Grafana:"
  say "$(public_url "${GRAFANA_PORT}")"
  say ""
fi
say "Mode:"
say "SIMULATION"
say ""
say "LLM:"
say "MOCK"
say ""
say "openCenter:"
say "MOCK"
say ""
say "Demo login:"
print_credentials "${ROOT_DIR}/.env"
say ""
say "Start:"
say "./start.sh"
say ""
say "Stop:"
say "./stop.sh"
rule
