#!/usr/bin/env bash
# Benchmark public API from your machine (inter-chunk gap, RTF, concurrency).
#
#   export API_URL=https://<lightning-public-host>:8080
#   export DEFAULT_USERNAME=dev
#   export DEFAULT_PASSWORD=devpassword
#
#   ./scripts/bench_remote.sh                    # stream @ 1,4 concurrent
#   ./scripts/bench_remote.sh all 8 1,4,8       # all modes, 8 reqs, concurrency levels
set -euo pipefail
cd "$(dirname "$0")/.."

MODE="${1:-stream}"
REQUESTS="${2:-8}"
CONCURRENCY="${3:-1,4}"
TAG="${4:-remote}"
TEXT="${BENCH_TEXT:-Hello, this is a streaming debug test for Orpheus TTS.}"
API_URL="${API_URL:?Set API_URL to your public Lightning URL, e.g. https://...:8080}"
OUT="bench_remote_out/${TAG}"

if ! python3 -c "import websocket" 2>/dev/null; then
  python3 -m pip install -q websocket-client
fi

echo "Target: ${API_URL}"
echo "Mode=${MODE} requests=${REQUESTS} concurrency=${CONCURRENCY}"

python3 scripts/bench_remote.py \
  --api-url "${API_URL}" \
  --mode "${MODE}" \
  --requests "${REQUESTS}" \
  --concurrency "${CONCURRENCY}" \
  --text "${TEXT}" \
  --out "${OUT}"
