#!/usr/bin/env bash
#
#   ./logs.sh                 backend + frontend
#   ./logs.sh backend         one service
#   ./logs.sh postgres redis  several

set -uo pipefail
source "$(dirname "$0")/scripts/lib.sh"

cd "$ROOT_DIR"
export ENV_FILE="${ROOT_DIR}/.env"
export COMPOSE_PROJECT="${POC_PROJECT}"
export COMPOSE_PROFILES_ARGS="--profile observability"

# Friendly aliases.
services=()
for arg in "$@"; do
  case "$arg" in
    opencenter | mock-opencenter) services+=(mock-opencenter) ;;
    ui | web) services+=(frontend) ;;
    api) services+=(backend) ;;
    db | postgres) services+=(postgres) ;;
    *) services+=("$arg") ;;
  esac
done

if [ ${#services[@]} -eq 0 ]; then
  services=(backend frontend)
fi

compose logs -f --tail=120 "${services[@]}"
