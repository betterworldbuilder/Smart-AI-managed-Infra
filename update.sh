#!/usr/bin/env bash
#
# Pull the latest code, rebuild and restart. Data is preserved.

set -uo pipefail
source "$(dirname "$0")/scripts/lib.sh"

cd "$ROOT_DIR"
require_docker

if [ -d "${ROOT_DIR}/.git" ]; then
  info "Pulling the latest code"
  git pull --ff-only || warn "git pull failed -- continuing with the working tree as it is"
else
  warn "not a git checkout; skipping git pull"
fi

export ENV_FILE="${ROOT_DIR}/.env"
export COMPOSE_PROJECT="${POC_PROJECT}"
export COMPOSE_PROFILES_ARGS="--profile observability"

info "Rebuilding images"
compose build || die "build failed"

info "Restarting services"
compose up -d || die "docker compose up failed"

# Schema migrations: the document store creates its own table on start-up, so
# there is nothing to run here yet. When real tables arrive, this is where
# `alembic upgrade head` goes.
info "Applying database migrations"
ok "schema is created on demand by the application (nothing to migrate)"

BACKEND_PORT="$(env_value "$ENV_FILE" BACKEND_PORT 8000)"
wait_for_http "http://localhost:${BACKEND_PORT}/healthz" 180 "backend" \
  || die "the backend did not come back -- ./logs.sh backend"

ok "updated. ./health.sh for a full check."
