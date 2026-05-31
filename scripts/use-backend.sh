#!/usr/bin/env bash
# Switch inference backend and recreate inference + api (full stack: use compose-up.sh).
set -euo pipefail
exec "$(dirname "$0")/compose-up.sh" "${1:-llamacpp}"
