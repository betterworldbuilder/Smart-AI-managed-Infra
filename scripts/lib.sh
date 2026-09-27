#!/usr/bin/env bash
# Shared helpers for every launcher script. Sourced, never executed directly.

set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="${ROOT_DIR}/docker-compose.yml"

# How the calling script was invoked. A file sourced without arguments sees
# the caller's "$@", so this lets require_docker re-run the same script under
# a freshly granted docker group -- no logout needed.
SCRIPT_PATH="$0"
SCRIPT_ARGS=("$@")
: "${USER:=$(id -un)}"   # unset under cron and in minimal containers

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

is_apt_linux() {
  # Ubuntu, Debian and derivatives -- where get.docker.com is the supported path.
  [ "$(uname -s)" = "Linux" ] && [ -r /etc/os-release ] || return 1
  ( . /etc/os-release
    case " ${ID:-} ${ID_LIKE:-} " in *" ubuntu "* | *" debian "*) exit 0 ;; *) exit 1 ;; esac )
}

print_docker_install_help() {
  say ""
  say "  Install Docker yourself:"
  if is_apt_linux; then
    say "    curl -fsSL https://get.docker.com | sudo sh"
    say "    sudo usermod -aG docker \$USER"
    say "    newgrp docker          # or log out and back in"
  else
    say "    https://docs.docker.com/get-docker/   (Windows/macOS: Docker Desktop)"
  fi
  say "  then re-run $(basename "$SCRIPT_PATH")."
}

rerun_under_docker_group() {
  # The docker group was granted after this shell started, so this process
  # cannot use it. Re-run the same script under it with `sg`, once.
  if [ -z "${_AIINFRA_DOCKER_REEXEC:-}" ] && command -v sg >/dev/null 2>&1; then
    info "Re-running $(basename "$SCRIPT_PATH") with the docker group (no logout needed)"
    export _AIINFRA_DOCKER_REEXEC=1
    exec sg docker -c "$(printf '%q ' "$SCRIPT_PATH" "${SCRIPT_ARGS[@]}")"
  fi
  die "Docker is installed but this shell cannot use it yet. Run: newgrp docker   (or log out and back in), then re-run $(basename "$SCRIPT_PATH")."
}

install_docker() {
  command -v curl >/dev/null 2>&1 || { sudo apt-get update -qq && sudo apt-get install -y -qq curl; }
  info "Installing Docker Engine and the Compose plugin (https://get.docker.com)"
  if ! curl -fsSL https://get.docker.com | sudo sh; then
    fail "the Docker install script failed"
    print_docker_install_help
    exit 1
  fi
  # systemd on a normal server; the init script where there is no systemd.
  sudo systemctl enable --now docker >/dev/null 2>&1 || sudo service docker start >/dev/null 2>&1 || true
  sudo usermod -aG docker "$USER"
  ok "Docker $(docker --version 2>/dev/null | awk '{print $3}' | tr -d ,) installed; $USER added to the docker group"
}

require_docker() {
  # 1. Not installed.
  if ! command -v docker >/dev/null 2>&1; then
    warn "Docker is not installed."
    if is_apt_linux && command -v sudo >/dev/null 2>&1; then
      if confirm "Install it now with the official script (uses sudo)?"; then
        install_docker
        rerun_under_docker_group
      fi
    fi
    print_docker_install_help
    exit 1
  fi

  # 2. Installed but unusable: a permission problem, or the daemon is down.
  if ! docker info >/dev/null 2>&1; then
    if [ -S /var/run/docker.sock ] && [ ! -w /var/run/docker.sock ]; then
      # Granted in /etc/group but not active in this shell (typical right
      # after an install), or never granted at all.
      if id -nG "$USER" 2>/dev/null | tr ' ' '\n' | grep -qx docker; then
        rerun_under_docker_group
      fi
      warn "$USER is not allowed to use Docker (not in the docker group)."
      if confirm "Add $USER to the docker group (uses sudo)?"; then
        sudo usermod -aG docker "$USER"
        rerun_under_docker_group
      fi
      die "Run: sudo usermod -aG docker \$USER && newgrp docker"
    fi
    if command -v sudo >/dev/null 2>&1 && [ "$(uname -s)" = "Linux" ] && ! grep -qi microsoft /proc/version 2>/dev/null; then
      info "The Docker daemon is not running -- starting it"
      sudo systemctl start docker >/dev/null 2>&1 || sudo service docker start >/dev/null 2>&1 || true
      local _
      for _ in 1 2 3 4 5 6 7 8 9 10; do docker info >/dev/null 2>&1 && break; sleep 1; done
    fi
    docker info >/dev/null 2>&1 \
      || die "The Docker daemon is not reachable. Start Docker Desktop, or: sudo systemctl start docker"
  fi

  # 3. Compose v2.
  if ! docker compose version >/dev/null 2>&1; then
    if is_apt_linux && command -v sudo >/dev/null 2>&1 && confirm "Install the Docker Compose plugin (uses sudo)?"; then
      sudo apt-get update -qq && sudo apt-get install -y -qq docker-compose-plugin
    fi
    docker compose version >/dev/null 2>&1 \
      || die "Docker Compose v2 is required ('docker compose'). On Ubuntu: sudo apt-get install docker-compose-plugin"
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
  # Shell environment wins over --env-file in Compose, so the detected public
  # address reaches ${PUBLIC_HOST} substitutions (Grafana's root URL).
  resolve_public_host
  # shellcheck disable=SC2086
  docker "${args[@]}" $COMPOSE_PROFILES_ARGS "$@"
}

# --- public address ----------------------------------------------------------
#
# Where a *browser* should go. Health checks and service-to-service calls keep
# using localhost -- they run on this machine. Only URLs shown to a person
# change.
#
# Resolution order:
#   1. PUBLIC_HOST from the environment or the env file
#   2. the EC2 instance metadata service (IMDSv2): public IPv4, then public DNS
#   3. localhost
#
# Resolve ONCE per script with `resolve_public_host` (not inside $(...), which
# is a subshell and cannot cache). Off EC2 the metadata probe costs at most one
# 1-second timeout; after that every lookup is instant.
#
# An instance with no public IP (private subnet behind a load balancer) gets
# nothing from the metadata service -- set PUBLIC_HOST to the load balancer's
# DNS name or your domain in that case.

resolve_public_host() {
  if [ -n "${PUBLIC_HOST:-}" ]; then
    export PUBLIC_HOST
    return
  fi
  local configured
  configured="$(env_value "${ENV_FILE:-${ROOT_DIR}/.env}" PUBLIC_HOST '')"
  if [ -n "$configured" ]; then
    PUBLIC_HOST="$configured"
    export PUBLIC_HOST
    return
  fi
  local token found=''
  token="$(curl -fsS --max-time 1 -X PUT 'http://169.254.169.254/latest/api/token' \
    -H 'X-aws-ec2-metadata-token-ttl-seconds: 60' 2>/dev/null || true)"
  if [ -n "$token" ]; then
    found="$(curl -fsS --max-time 2 -H "X-aws-ec2-metadata-token: ${token}" \
      'http://169.254.169.254/latest/meta-data/public-ipv4' 2>/dev/null || true)"
    if [ -z "$found" ]; then
      found="$(curl -fsS --max-time 2 -H "X-aws-ec2-metadata-token: ${token}" \
        'http://169.254.169.254/latest/meta-data/public-hostname' 2>/dev/null || true)"
    fi
  fi
  PUBLIC_HOST="${found:-localhost}"
  export PUBLIC_HOST
}

ec2_imds() {
  # ec2_imds PATH -> a value from the EC2 instance metadata service, or nothing
  local token
  token="$(curl -fsS --max-time 1 -X PUT 'http://169.254.169.254/latest/api/token' \
    -H 'X-aws-ec2-metadata-token-ttl-seconds: 60' 2>/dev/null || true)"
  [ -n "$token" ] || return 0
  curl -fsS --max-time 2 -H "X-aws-ec2-metadata-token: ${token}" \
    "http://169.254.169.254/latest/meta-data/$1" 2>/dev/null || true
}

print_public_access_hint() {
  # print_public_access_hint PORT
  #
  # The script has already proved the stack answers on this instance. If a
  # browser still can't reach it, the cause is almost always outside the
  # instance -- so say exactly what to check, with this instance's own
  # security group and a command that runs as-is on the operator's laptop.
  is_public_host || return 0
  local port="${1:-3000}" mac='' sgs='' sg='' region=''
  mac="$(ec2_imds mac)"
  if [ -n "$mac" ]; then
    sgs="$(ec2_imds "network/interfaces/macs/${mac}/security-group-ids" | tr '\n' ' ' | sed 's/ *$//')"
    region="$(ec2_imds placement/region)"
  fi
  sg="${sgs%% *}"
  say ""
  say "${C_BOLD}Page not loading in your browser?${C_RESET} The stack is up -- checked from this instance."
  say "  1. Use $(public_url "$port"), not localhost (on your laptop that is your laptop)."
  say "  2. Type the http:// yourself -- these ports serve plain HTTP, not HTTPS."
  if [ -n "${sg:-}" ]; then
    say "  3. Allow your IP in this instance's security group (${sgs}). On your laptop:"
    say "       aws ec2 authorize-security-group-ingress --region ${region} --group-id ${sg} \\"
    say "         --protocol tcp --port ${port} --cidr \"\$(curl -s https://checkip.amazonaws.com)/32\""
    say "     or in the console: EC2 > Security Groups > ${sg} > Edit inbound rules >"
    say "     Custom TCP ${port}, source My IP. Same for 8000 (API docs) and 3001 (Grafana) if wanted."
  else
    say "  3. Allow inbound TCP ${port} from your IP in the firewall / security group in front of this host."
  fi
  say "  Test from your laptop: curl -m 5 $(public_url "$port" /healthz)   (200 = reachable, timeout = blocked)"
}

detect_public_host() {
  # Fast path once resolved in the parent shell.
  [ -n "${PUBLIC_HOST:-}" ] || resolve_public_host
  printf '%s' "$PUBLIC_HOST"
}

# public_url PORT [PATH]
public_url() {
  printf 'http://%s:%s%s' "$(detect_public_host)" "$1" "${2:-}"
}

is_public_host() {
  case "$(detect_public_host)" in
    localhost | 127.0.0.1 | '') return 1 ;;
    *) return 0 ;;
  esac
}

# --- credentials on a public host -------------------------------------------
#
# admin/admin is fine on a laptop and unacceptable on the internet. When the
# stack is reachable from outside, replace any default credential still in the
# env file with a random one, and say so.

random_secret() {
  # 20 alphanumeric characters. Only ~24% of random bytes are alphanumeric,
  # so read plenty -- 64 bytes regularly yielded fewer than 20.
  head -c 512 /dev/urandom | tr -dc 'A-Za-z0-9' | head -c 20
}

set_env_value() {
  # set_env_value FILE KEY VALUE
  local file="$1" key="$2" value="$3" tmp
  tmp="$(mktemp)"
  if grep -qE "^${key}=" "$file"; then
    sed "s|^${key}=.*|${key}=${value}|" "$file" >"$tmp" && mv "$tmp" "$file"
  else
    cp "$file" "$tmp" && printf '%s=%s\n' "$key" "$value" >>"$tmp" && mv "$tmp" "$file"
  fi
}

harden_public_credentials() {
  # harden_public_credentials ENV_FILE
  #
  # The root .env is the source of truth. Derived files (.env.poc, .env.mvp)
  # inherit its values, so every script and every mode agree on one password.
  local file="$1" changed=0 source="${ROOT_DIR}/.env"
  is_public_host || return 0
  [ -f "$file" ] || return 0

  if [ "$file" != "$source" ] && [ -f "$source" ]; then
    harden_public_credentials "$source"
  fi

  _harden_one() {
    # _harden_one KEY DEFAULT GENERATOR
    local key="$1" default="$2" generator="$3" current inherited
    current="$(env_value "$file" "$key" "$default")"
    [ "$current" = "$default" ] || return 0
    inherited=''
    [ "$file" != "$source" ] && inherited="$(env_value "$source" "$key" "$default")"
    if [ -n "$inherited" ] && [ "$inherited" != "$default" ]; then
      set_env_value "$file" "$key" "$inherited"
    else
      set_env_value "$file" "$key" "$($generator)"
    fi
    changed=1
  }
  _long_secret() { printf '%s%s' "$(random_secret)" "$(random_secret)"; }

  # The app login only matters when it is switched on.
  if [ "$(env_value "$file" AUTH_ENABLED false)" = "true" ]; then
    _harden_one AUTH_PASSWORD admin random_secret
    _harden_one AUTH_SECRET poc-insecure-signing-key _long_secret
  fi
  _harden_one GRAFANA_PASSWORD admin random_secret
  if [ "$changed" = "1" ]; then
    warn "public host detected ($(detect_public_host)): default passwords replaced in $(basename "$file")"
  fi
}

login_payload() {
  # login_payload ENV_FILE -> the JSON body for POST /api/auth/login
  local file="${1:-${ENV_FILE:-${ROOT_DIR}/.env}}"
  printf '{"username":"%s","password":"%s"}' \
    "$(env_value "$file" AUTH_USERNAME admin)" \
    "$(env_value "$file" AUTH_PASSWORD admin)"
}

print_credentials() {
  # print_credentials ENV_FILE
  local file="$1"
  local user pass
  if [ "$(env_value "$file" AUTH_ENABLED false)" != "true" ]; then
    say "Login:       none -- the app opens directly (AUTH_ENABLED=false)"
    if is_public_host; then
      say "             anyone who can reach the UI can use it: limit the"
      say "             security group to your own IP"
    fi
    return 0
  fi
  user="$(env_value "$file" AUTH_USERNAME admin)"
  pass="$(env_value "$file" AUTH_PASSWORD admin)"
  if [ "$pass" = "admin" ]; then
    say "Login:       ${user} / admin   (POC ONLY -- local use)"
  else
    say "Login:       ${user} / ${pass}"
    say "             (stored in $(basename "$file"); change AUTH_PASSWORD there)"
  fi
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
