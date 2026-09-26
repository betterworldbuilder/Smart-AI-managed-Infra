#!/usr/bin/env bash
#
# Stop the POC. Data (PostgreSQL, Redis, Grafana) is preserved.

set -uo pipefail
source "$(dirname "$0")/scripts/lib.sh"

cd "$ROOT_DIR"
require_docker

export ENV_FILE="${ROOT_DIR}/.env"
export COMPOSE_PROJECT="${POC_PROJECT}"
export COMPOSE_PROFILES_ARGS="--profile observability"

info "Stopping the POC (volumes are kept)"
compose stop
ok "stopped -- start it again with ./start.sh"
