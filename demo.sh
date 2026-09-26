#!/usr/bin/env bash
#
# The single command: prerequisites -> install -> start -> health -> seed -> URL.
#
#   git clone <repo> && cd gpu-native-infra && ./demo.sh

set -uo pipefail
source "$(dirname "$0")/scripts/lib.sh"

cd "$ROOT_DIR"

info "Checking prerequisites"
require_docker
ok "Docker is ready"

"${ROOT_DIR}/install.sh" "$@" || die "installation failed"
"${ROOT_DIR}/start.sh" "$@" || die "the stack did not start"
"${ROOT_DIR}/health.sh" || warn "health check reported problems -- continuing"

ENV_FILE="${ROOT_DIR}/.env"
resolve_public_host
BACKEND_PORT="$(env_value "$ENV_FILE" BACKEND_PORT 8000)"
FRONTEND_PORT="$(env_value "$ENV_FILE" FRONTEND_PORT 3000)"
API="http://localhost:${BACKEND_PORT}/api"

info "Seeding the demo"
TOKEN="$(curl -fsS --max-time 10 -X POST "${API}/auth/login" \
  -H 'Content-Type: application/json' \
  -d "$(login_payload)" 2>/dev/null | pyjson "data['access_token']")"

if [ -n "$TOKEN" ]; then
  # Run the scripted scenarios through the real Copilot and Governor so the UI
  # has recommendations to look at. Nothing is deployed -- that needs a human.
  for scenario in A D E; do
    RESULT="$(curl -fsS --max-time 60 -X POST -H "Authorization: Bearer ${TOKEN}" \
      "${API}/scenarios/${scenario}/run" 2>/dev/null |
      pyjson "data.get('recommended_runtime') or 'none'")"
    ok "scenario ${scenario}: ${RESULT}"
  done
else
  warn "could not sign in to seed the demo scenarios"
fi

say ""
rule
say "${C_BOLD}GPU NATIVE INFRA POC READY${C_RESET}"
rule
say ""
say "Open:"
say "$(public_url "${FRONTEND_PORT}")"
say ""
say "Try:"
say "\"Deploy a private Llama service for 100 users\""
say ""
say "Mode:"
say "Simulation"
say ""
say "AI:"
say "Mock Copilot"
say ""
say "Deployment:"
say "Mock openCenter"
say ""
say "No GPU, OpenStack or Kubernetes required."
rule
