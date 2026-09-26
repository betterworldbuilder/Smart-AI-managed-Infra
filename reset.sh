#!/usr/bin/env bash
#
# Delete all POC data and rebuild the simulation from scratch.

set -uo pipefail
source "$(dirname "$0")/scripts/lib.sh"

cd "$ROOT_DIR"
require_docker

say "This will delete all POC data and reset the simulation."
say "  - PostgreSQL volume (conversations, recommendations, deployments, audit)"
say "  - Redis volume"
say "  - Grafana and Prometheus data"
say "  - generated GitOps artefacts"
if ! confirm "Continue?"; then
  say "Cancelled."
  exit 0
fi

export ENV_FILE="${ROOT_DIR}/.env"
export COMPOSE_PROJECT="${POC_PROJECT}"
export COMPOSE_PROFILES_ARGS="--profile observability"

info "Removing containers and volumes"
compose down -v

info "Clearing generated GitOps artefacts"
rm -rf "${ROOT_DIR}/deployments/mvp/flux/workloads/generated"
mkdir -p "${ROOT_DIR}/deployments/mvp/flux/workloads/generated"
ok "generated workloads cleared"

info "Recreating the environment"
compose up -d

BACKEND_PORT="$(env_value "$ENV_FILE" BACKEND_PORT 8000)"
wait_for_http "http://localhost:${BACKEND_PORT}/healthz" 180 "backend" \
  || die "the backend did not come back -- ./logs.sh backend"

# The simulated inventory and demo scenarios are read from disk at start-up, so
# a fresh container is already a fresh datacenter; confirm it.
GPUS="$(json_field "http://localhost:${BACKEND_PORT}/api/system/capabilities" "data.get('mode_label','?')")"
ok "simulation reloaded (${GPUS})"

exec "${ROOT_DIR}/start.sh"
