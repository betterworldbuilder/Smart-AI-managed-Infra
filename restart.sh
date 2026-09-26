#!/usr/bin/env bash
set -uo pipefail
source "$(dirname "$0")/scripts/lib.sh"

cd "$ROOT_DIR"
"${ROOT_DIR}/stop.sh" "$@"
exec "${ROOT_DIR}/start.sh" "$@"
