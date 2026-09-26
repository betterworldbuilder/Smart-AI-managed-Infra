#!/usr/bin/env bash
#
# Interactive setup: choose a deployment mode and an AI provider, then write
# the matching .env. Everything it produces can also be edited by hand.

set -uo pipefail
source "$(dirname "$0")/scripts/lib.sh"

cd "$ROOT_DIR"
TARGET="${ROOT_DIR}/.env"

rule
say "${C_BOLD}GPU Native Infra Setup${C_RESET}"
rule
say ""
say "Choose deployment mode:"
say ""
say "1. Simulation            (default -- nothing real is required)"
say "2. Real Kubernetes       (local kind cluster, the MVP)"
say "3. Real OpenStack        (existing Genestack/OpenStack cloud)"
say "4. Real openCenter       (existing openCenter installation)"
say "5. Full infrastructure   (all of the above)"
say ""
read -r -p "Selection [1]: " MODE
MODE="${MODE:-1}"

say ""
say "Use mock AI or local Ollama?"
say ""
say "1. Mock     (deterministic, no external service)"
say "2. Ollama   (local model; ./enable-ollama.sh pulls one for you)"
say ""
read -r -p "Selection [1]: " AI
AI="${AI:-1}"

cp "${ROOT_DIR}/.env.example" "$TARGET"

set_env() {
  local key="$1" value="$2"
  if grep -qE "^${key}=" "$TARGET"; then
    # Portable in-place edit (GNU and BSD sed disagree about -i).
    local tmp
    tmp="$(mktemp)"
    sed "s|^${key}=.*|${key}=${value}|" "$TARGET" >"$tmp" && mv "$tmp" "$TARGET"
  else
    printf '%s=%s\n' "$key" "$value" >>"$TARGET"
  fi
}

case "$MODE" in
  2)
    set_env INFRA_MODE mvp
    set_env SIMULATION_MODE false
    set_env OPEN_CENTER_MODE compat
    say ""
    ok "MVP mode: real kind Kubernetes + Flux; OpenStack and Genestack stay simulated."
    say "   Start it with ./startmvp.sh (needs kind, kubectl and helm)."
    ;;
  3)
    set_env OPENSTACK_MODE real
    set_env CEPH_MODE real
    read -r -p "OS_AUTH_URL: " OS_AUTH_URL
    read -r -p "OS_USERNAME: " OS_USERNAME
    read -r -p "OS_PROJECT_NAME: " OS_PROJECT
    set_env OS_AUTH_URL "${OS_AUTH_URL}"
    set_env OS_USERNAME "${OS_USERNAME}"
    set_env OS_PROJECT_NAME "${OS_PROJECT}"
    warn "Set OS_PASSWORD in your shell or a Docker secret -- never commit it."
    ;;
  4)
    set_env OPEN_CENTER_MODE real
    read -r -p "OPEN_CENTER_URL: " OC_URL
    set_env OPEN_CENTER_URL "${OC_URL:-http://mock-opencenter:8080}"
    warn "Set OPEN_CENTER_TOKEN in your shell or a Docker secret -- never commit it."
    ;;
  5)
    set_env INFRA_MODE mvp
    set_env SIMULATION_MODE false
    set_env OPENSTACK_MODE real
    set_env CEPH_MODE real
    set_env GENESTACK_MODE real
    set_env OPEN_CENTER_MODE real
    warn "Full mode expects real credentials in the environment. See config/*.example."
    ;;
  *)
    set_env INFRA_MODE simulation
    set_env SIMULATION_MODE true
    set_env OPEN_CENTER_MODE mock
    ok "Simulation mode: the complete POC with no external dependency."
    ;;
esac

if [ "$AI" = "2" ]; then
  set_env LLM_PROVIDER ollama
  set_env LLM_BASE_URL "http://host.docker.internal:11434"
  read -r -p "Ollama model [qwen3:8b]: " MODEL
  set_env LLM_MODEL "${MODEL:-qwen3:8b}"
  say ""
  ok "Ollama selected. Run ./enable-ollama.sh to install and pull the model."
  say "   If Ollama is unreachable the Copilot falls back to the mock provider."
else
  set_env LLM_PROVIDER mock
  ok "Mock AI provider selected."
fi

say ""
rule
ok "wrote $(basename "$TARGET")"
say ""
say "Next:"
say "  ./install.sh     build and start"
say "  ./start.sh       start"
say "  ./startmvp.sh    (MVP mode only) create the kind cluster"
rule
