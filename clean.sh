#!/usr/bin/env bash
#
# Remove every container, network, volume and generated local file this project
# created. The source repository is never touched.

set -uo pipefail
source "$(dirname "$0")/scripts/lib.sh"

cd "$ROOT_DIR"
require_docker

say "This removes:"
say "  - POC containers, networks and volumes (project ${POC_PROJECT})"
say "  - MVP application resources (project ${MVP_PROJECT})"
say "  - generated local configuration (.env, .env.poc, .env.mvp, generated GitOps files)"
say ""
say "It does NOT delete the kind cluster (use ./stopmvp.sh --destroy) and it"
say "never deletes source files."
if ! confirm "Continue?"; then
  say "Cancelled."
  exit 0
fi

for project in "$POC_PROJECT" "$MVP_PROJECT"; do
  info "Removing docker resources for ${project}"
  COMPOSE_PROJECT="$project" COMPOSE_PROFILES_ARGS="--profile observability" \
    ENV_FILE="${ROOT_DIR}/.env" compose down -v --remove-orphans 2>/dev/null || true
done

info "Removing generated local configuration"
rm -f "${ROOT_DIR}/.env" "${ROOT_DIR}/deployments/poc/.env.poc" "${ROOT_DIR}/deployments/mvp/.env.mvp"
rm -rf "${ROOT_DIR}/deployments/mvp/flux/workloads/generated"
ok "local configuration removed (.env.example is untouched)"

info "Pruning dangling images built by this project"
docker image prune -f --filter "label=com.docker.compose.project=${POC_PROJECT}" >/dev/null 2>&1 || true

ok "clean. Run ./install.sh to start over."
