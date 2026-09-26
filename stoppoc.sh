#!/usr/bin/env bash
#
# Stop the POC only. The MVP (kind cluster, project gpuinfra-mvp) is untouched.

set -uo pipefail
source "$(dirname "$0")/scripts/lib.sh"

cd "$ROOT_DIR"
require_docker

DESTROY=0
for arg in "$@"; do
  [ "$arg" = "--destroy" ] && DESTROY=1
done

export ENV_FILE="${ROOT_DIR}/deployments/poc/.env.poc"
[ -f "$ENV_FILE" ] || export ENV_FILE="${ROOT_DIR}/.env"
export COMPOSE_PROJECT="${POC_PROJECT}"
export COMPOSE_PROFILES_ARGS="--profile observability"

if [ "$DESTROY" = "1" ]; then
  say "This removes the POC containers AND their volumes (all POC data)."
  if ! confirm "Continue?"; then
    say "Cancelled."
    exit 0
  fi
  compose down -v
  ok "POC removed, volumes deleted"
else
  info "Stopping POC services (project ${POC_PROJECT}); data is preserved"
  compose stop
  ok "POC stopped -- ./startpoc.sh to bring it back"
fi
