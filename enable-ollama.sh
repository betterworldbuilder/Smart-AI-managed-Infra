#!/usr/bin/env bash
#
# Optional: run the Copilot against a local Ollama model instead of the
# deterministic mock provider. The POC keeps working if Ollama is absent --
# the provider falls back to the mock automatically.

set -uo pipefail
source "$(dirname "$0")/scripts/lib.sh"

cd "$ROOT_DIR"
MODEL="${1:-${OLLAMA_MODEL:-qwen3:8b}}"
ENV_FILE="${ROOT_DIR}/.env"
[ -f "$ENV_FILE" ] || ensure_env_file "$ENV_FILE"

info "Checking for Ollama"
if ! command -v ollama >/dev/null 2>&1; then
  warn "Ollama is not installed."
  say ""
  say "  Linux / WSL:  curl -fsSL https://ollama.com/install.sh | sh"
  say "  macOS:        brew install ollama"
  say "  Windows:      https://ollama.com/download"
  say ""
  if ! confirm "Continue and configure the POC for Ollama anyway?"; then
    exit 1
  fi
else
  ok "ollama $(ollama --version 2>/dev/null | head -n1)"
  if ! curl -fsS --max-time 3 http://localhost:11434/api/tags >/dev/null 2>&1; then
    info "Starting the Ollama service"
    (ollama serve >/dev/null 2>&1 &) || true
    sleep 3
  fi
  info "Pulling ${MODEL} (this can take a while)"
  ollama pull "$MODEL" || warn "could not pull ${MODEL}; the Copilot will fall back to the mock"
fi

set_env() {
  local key="$1" value="$2" tmp
  tmp="$(mktemp)"
  if grep -qE "^${key}=" "$ENV_FILE"; then
    sed "s|^${key}=.*|${key}=${value}|" "$ENV_FILE" >"$tmp" && mv "$tmp" "$ENV_FILE"
  else
    printf '%s=%s\n' "$key" "$value" >>"$ENV_FILE"
  fi
}

set_env LLM_PROVIDER ollama
set_env LLM_MODEL "$MODEL"
set_env LLM_BASE_URL "http://host.docker.internal:11434"
ok "configured LLM_PROVIDER=ollama LLM_MODEL=${MODEL}"

info "Restarting the backend so it picks up the new provider"
export ENV_FILE COMPOSE_PROJECT="${POC_PROJECT}"
compose up -d backend >/dev/null 2>&1 || warn "could not restart the backend -- run ./restart.sh"

BACKEND_PORT="$(env_value "$ENV_FILE" BACKEND_PORT 8000)"
wait_for_http "http://localhost:${BACKEND_PORT}/healthz" 120 "backend" || true
PROVIDER="$(json_field "http://localhost:${BACKEND_PORT}/api/system/capabilities" \
  "data.get('llm_provider','?')")"
ok "Copilot provider is now: ${PROVIDER}"
say ""
say "Switch back with: LLM_PROVIDER=mock in .env, then ./restart.sh"
