#!/usr/bin/env bash
# End-to-end RTF benchmark: async, HTTP stream, live WS (+ optional gRPC).
set -euo pipefail
cd "$(dirname "$0")/.."
export COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.yml:docker-compose.llamacpp-gpu.yml}"
TAG="${1:-llamacpp_q8}"
TEXT="${BENCH_TEXT:-Hello, this is a streaming debug test for Orpheus TTS.}"
API_URL_HOST="${API_URL:-http://127.0.0.1:8080}"
API_URL_CONTAINER="${API_URL_CONTAINER:-http://api:8080}"
WITH_GRPC="${BENCH_GRPC:-1}"

echo "Backend: llamacpp | GGUF: ${ORPHEUS_GGUF_MODEL:-Orpheus-3b-FT-Q8_0.gguf}"
echo "Tag: ${TAG}"

if [[ -z "${API_KEY:-}" ]]; then
  API_KEY=$(docker compose logs api 2>/dev/null | sed -n 's/.*default API key (save now): \(sk-[^ ]*\).*/\1/p' | tail -1 || true)
fi
if [[ -z "${API_KEY:-}" ]]; then
  USER="${DEFAULT_USERNAME:-dev}"
  PASS="${DEFAULT_PASSWORD:-devpassword}"
  API_KEY=$(curl -sf -X POST "${API_URL_HOST}/v1/auth/login" \
    -H "Content-Type: application/json" \
    -d "{\"username\":\"${USER}\",\"password\":\"${PASS}\"}" \
    | python3 -c "import sys,json; print(json.load(sys.stdin)['apiKey'])")
fi
if [[ -z "${API_KEY:-}" ]]; then
  echo "Set API_KEY or DEFAULT_USERNAME/DEFAULT_PASSWORD for login"
  exit 1
fi

docker compose exec -T inference mkdir -p /app/scripts
docker compose cp scripts/bench_all_modes.py inference:/app/scripts/bench_all_modes.py
docker compose cp scripts/debug_pcm_stream.py inference:/app/scripts/debug_pcm_stream.py
docker compose exec -T inference pip install -q websocket-client

OUT="/tmp/bench_${TAG}"
ARGS=(--api-url "$API_URL_CONTAINER" --api-key "$API_KEY" --text "$TEXT" --out-dir "$OUT")
if [[ "$WITH_GRPC" == "1" ]]; then
  ARGS+=(--grpc --grpc-addr 127.0.0.1:50051)
fi

if [[ "${BENCH_SKIP_WARMUP:-0}" != "1" ]]; then
  echo "==> warm-up (gRPC stream only)"
  docker compose exec -T inference python -c "
import sys; sys.path.insert(0,'/app/scripts')
from debug_pcm_stream import capture_grpc_synthesize
capture_grpc_synthesize('127.0.0.1:50051', '''${TEXT}''', 'tara')
" 2>/dev/null || true
  sleep 2
fi

echo "==> measured run (async, stream, live${WITH_GRPC:+, gRPC})"
docker compose exec -T inference python /app/scripts/bench_all_modes.py "${ARGS[@]}"
