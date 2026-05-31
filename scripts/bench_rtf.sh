#!/usr/bin/env bash
# End-to-end RTF benchmark: async, HTTP stream, live WS (+ optional gRPC).
set -euo pipefail
cd "$(dirname "$0")/.."
export COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.yml:docker-compose.llamacpp-gpu.yml}"
TAG="${1:-llamacpp_q8}"
TEXT="${BENCH_TEXT:-Hello, this is a streaming debug test for Orpheus TTS.}"
API_URL="${API_URL:-http://127.0.0.1:8080}"
WITH_GRPC="${BENCH_GRPC:-1}"

echo "Backend: llamacpp | GGUF: ${ORPHEUS_GGUF_MODEL:-Orpheus-3b-FT-Q8_0.gguf}"
echo "Tag: ${TAG}"

if [[ -z "${API_KEY:-}" ]]; then
  API_KEY=$(docker compose logs api 2>/dev/null | sed -n 's/.*default API key (save now): \(sk-[^ ]*\).*/\1/p' | tail -1 || true)
fi
if [[ -z "${API_KEY:-}" ]]; then
  echo "Set API_KEY or ensure api logged default key on first boot"
  exit 1
fi

pip install -q websocket-client 2>/dev/null || python3 -m pip install -q websocket-client

OUT="/tmp/bench_${TAG}"
ARGS=(--api-url "$API_URL" --api-key "$API_KEY" --text "$TEXT" --out-dir "$OUT")
if [[ "$WITH_GRPC" == "1" ]]; then
  ARGS+=(--grpc --grpc-addr "${GRPC_ADDR:-127.0.0.1:50051}")
fi

echo "==> warm-up (HTTP stream)"
python3 scripts/bench_all_modes.py --api-url "$API_URL" --api-key "$API_KEY" \
  --text "$TEXT" --out-dir "/tmp/bench_${TAG}_warmup" 2>/dev/null | tail -5 || true
sleep 2

echo "==> measured run"
python3 scripts/bench_all_modes.py "${ARGS[@]}"
