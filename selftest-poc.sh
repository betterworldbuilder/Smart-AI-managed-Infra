#!/usr/bin/env bash
#
# The POC self test. Identical to ./selftest.sh, named for symmetry with
# ./selftest-mvp.sh.
set -uo pipefail
exec "$(dirname "$0")/selftest.sh" "$@"
