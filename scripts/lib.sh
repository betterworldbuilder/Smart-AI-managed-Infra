#!/usr/bin/env bash
# Shared helpers for every launcher script. Sourced, never executed directly.

set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="${ROOT_DIR}/docker-compose.yml"

POC_PROJECT="gpuinfra-poc"
MVP_PROJECT="gpuinfra-mvp"

if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
  C_RESET="\033[0m"; C_BOLD="\033[1m"; C_DIM="\033[2m"
  C_RED="\033[31m"; C_GREEN="\033[32m"; C_YELLOW="\033[33m"; C_BLUE="\033[34m"
else
  C_RESET=""; C_BOLD=""; C_DIM=""; C_RED=""; C_GREEN=""; C_YELLOW=""; C_BLUE=""
fi

say()   { printf '%b\n' "$*"; }
info()  { printf '%b\n' "${C_BLUE}==>${C_RESET} $*"; }
ok()    { printf '%b\n' "${C_GREEN}  ok${C_RESET} $*"; }
warn()  { printf '%b\n' "${C_YELLOW}  !!${C_RESET} $*"; }
fail()  { printf '%b\n' "${C_RED} FAIL${C_RESET} $*"; }
die()   { fail "$*"; exit 1; }

rule() { printf '%s\n' "=================================================="; }

# --- prerequisites ---------------------------------------------------------

require_docker() {
  if ! command -v docker >/dev/null 2>&1; then
    die "Docker is not installed. See https://docs.docker.com/get-docker/ (on Windows/macOS install Docker Desktop)."
  fi
  if ! docker info >/dev/null 2>&1; then
    die "The Docker daemon is not reachable. Start Docker Desktop, or: sudo systemctl start docker"
  fi
  if ! docker compose version >/dev/null 2>&1; then
    die "Docker Compose v2 is required ('docker compose'). Update Docker, or install the compose plugin."
  fi
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "'$1' is required but not installed. $2"
}

# --- environment -----------------------------------------------------------

ensure_env_file() {
  # $1: target env file, $2: optional mode override banner
  local target="$1"
  if [ ! -f "$target" ]; then
    cp "${ROOT_DIR}/.env.example" "$target"
    ok "created $(basename "$target") from .env.example"
  else
    ok "$(basename "$target") already exists (left untouched)"
  fi
}

env_value() {
  # env_value FILE KEY DEFAULT
  local file="$1" key="$2" default="${3:-}"
  if [ -f "$file" ]; then
    local line
    line="$(grep -E "^${key}=" "$file" | tail -n1 || true)"
    if [ -n "$line" ]; then
      printf '%s' "${line#*=}"
      return
    fi
  fi
  printf '%s' "$default"
}

# --- compose ---------------------------------------------------------------

: "${COMPOSE_PROJECT:=${POC_PROJECT}}"
: "${ENV_FILE:=${ROOT_DIR}/.env}"
: "${COMPOSE_PROFILES_ARGS:=}"

compose() {
  local args=(compose -p "$COMPOSE_PROJECT" -f "$COMPOSE_FILE")
  [ -f "$ENV_FILE" ] && args+=(--env-file "$ENV_FILE")
  # shellcheck disable=SC2086
  docker "${args[@]}" $COMPOSE_PROFILES_ARGS "$@"
}

# --- waiting ---------------------------------------------------------------

wait_for_http() {
  # wait_for_http URL TIMEOUT_SECONDS LABEL
  local url="$1" timeout="${2:-120}" label="${3:-$1}"
  local waited=0
  printf '   waiting for %s ' "$label"
  while [ "$waited" -lt "$timeout" ]; do
    if curl -fsS --max-time 3 "$url" >/dev/null 2>&1; then
      printf ' %bok%b\n' "$C_GREEN" "$C_RESET"
      return 0
    fi
    printf '.'
    sleep 2
    waited=$((waited + 2))
  done
  printf ' %btimeout%b\n' "$C_RED" "$C_RESET"
  return 1
}

http_status() {
  curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$1" 2>/dev/null || echo 000
}

# A JSON reader. Prefers the host's python3; falls back to the backend
# container so the scripts work on a machine with Docker and nothing else.
if command -v python3 >/dev/null 2>&1; then
  PY_CMD=(python3)
elif command -v python >/dev/null 2>&1; then
  PY_CMD=(python)
else
  PY_CMD=(docker run --rm -i python:3.12-slim python3)
fi

pyjson() {
  # pyjson EXPRESSION  -- reads JSON on stdin, prints the expression
  "${PY_CMD[@]}" -c "
import json,sys
try:
    data = json.load(sys.stdin)
except Exception:
    print('')
    sys.exit(0)
print($1)
" 2>/dev/null || printf ''
}

json_field() {
  # json_field URL PYTHON_EXPRESSION  (expression receives `data`)
  local url="$1" expr="$2"
  curl -fsS --max-time 5 "$url" 2>/dev/null | "${PY_CMD[@]}" -c "
import json,sys
try:
    data = json.load(sys.stdin)
except Exception:
    print('')
    sys.exit(0)
print($expr)
" 2>/dev/null || printf ''
}

confirm() {
  local prompt="$1"
  if [ "${ASSUME_YES:-0}" = "1" ]; then
    return 0
  fi
  read -r -p "$prompt [y/N] " reply
  case "$reply" in
    [yY] | [yY][eE][sS]) return 0 ;;
    *) return 1 ;;
  esac
}

ports_in_use() {
  # Report any requested port already bound, so failures are legible.
  local port
  for port in "$@"; do
    if command -v ss >/dev/null 2>&1; then
      if ss -ltn "sport = :${port}" 2>/dev/null | grep -q LISTEN; then
        warn "port ${port} is already in use (see docs/troubleshooting.md)"
      fi
    fi
  done
}
