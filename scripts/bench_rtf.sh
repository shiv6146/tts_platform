#!/usr/bin/env bash
# RTF benchmark: (1) direct gRPC inference, (2) API async/stream/live.
set -euo pipefail
cd "$(dirname "$0")/.."
export COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.yml:docker-compose.llamacpp-gpu.yml}"

TAG="${1:-llamacpp_q8}"
TEXT="${BENCH_TEXT:-Hello, this is a streaming debug test for Orpheus TTS.}"
API_URL_HOST="${API_URL:-http://127.0.0.1:8080}"
API_URL_CONTAINER="http://api:8080"
GRPC_ADDR_CONTAINER="127.0.0.1:50051"

echo "COMPOSE_FILE=$COMPOSE_FILE"
echo "Tag: ${TAG} | GGUF: ${ORPHEUS_GGUF_MODEL:-Orpheus-3b-FT-Q8_0.gguf}"

copy_scripts() {
  docker compose exec -T inference mkdir -p /app/scripts
  docker compose cp scripts/bench_all_modes.py inference:/app/scripts/bench_all_modes.py
  docker compose cp scripts/debug_pcm_stream.py inference:/app/scripts/debug_pcm_stream.py
  docker compose exec -T inference pip install -q websocket-client 2>/dev/null || true
}

wait_inference() {
  for i in $(seq 1 3); do
    if docker compose exec -T inference python -c "
import grpc,sys
sys.path.insert(0,'/app/inference')
from tts.v1 import inference_pb2,inference_pb2_grpc
r=inference_pb2_grpc.TTSInferenceStub(grpc.insecure_channel('127.0.0.1:50051')).Health(inference_pb2.HealthRequest(),timeout=30)
exit(0 if r.ok else 1)" 2>/dev/null; then
      return 0
    fi
    sleep 5
  done
  echo "ERROR: inference gRPC not healthy. Run: ./scripts/compose-up.sh llamacpp" >&2
  exit 1
}

check_api_from_inference() {
  echo "Checking API reachability from inference container -> ${API_URL_CONTAINER} ..."
  docker compose exec -T inference python -c "
import urllib.request
url = '${API_URL_CONTAINER}/health'
with urllib.request.urlopen(url, timeout=15) as r:
    assert r.status == 200, r.status
print('API OK:', url)
"
}

check_api_from_host() {
  echo "Checking API reachability from host -> ${API_URL_HOST} ..."
  curl -sf "${API_URL_HOST}/health" >/dev/null
}

fetch_api_key() {
  if [[ -n "${API_KEY:-}" ]]; then
    return 0
  fi
  API_KEY=$(docker compose logs api 2>/dev/null | sed -n 's/.*default API key (save now): \(sk-[^ ]*\).*/\1/p' | tail -1 || true)
  if [[ -z "${API_KEY:-}" ]]; then
    USER="${DEFAULT_USERNAME:-dev}"
    PASS="${DEFAULT_PASSWORD:-devpassword}"
    check_api_from_host
    API_KEY=$(curl -sf -X POST "${API_URL_HOST}/v1/auth/login" \
      -H "Content-Type: application/json" \
      -d "{\"username\":\"${USER}\",\"password\":\"${PASS}\"}" \
      | python3 -c "import sys,json; print(json.load(sys.stdin)['apiKey'])")
  fi
  if [[ -z "${API_KEY:-}" ]]; then
    echo "ERROR: set API_KEY or DEFAULT_USERNAME/DEFAULT_PASSWORD" >&2
    exit 1
  fi
}

copy_scripts
wait_inference

OUT_GRPC="/tmp/bench_${TAG}_grpc"
OUT_API="/tmp/bench_${TAG}_api"

echo ""
echo "========== Phase 1: gRPC (inference direct) =========="
docker compose exec -T inference python /app/scripts/debug_pcm_stream.py \
  --grpc --grpc-addr "${GRPC_ADDR_CONTAINER}" \
  --text "$TEXT" --out-dir "$OUT_GRPC"

echo ""
echo "========== Phase 2: API (async, stream, live) =========="
check_api_from_host
check_api_from_inference
fetch_api_key

docker compose exec -T inference python /app/scripts/bench_all_modes.py \
  --api-only \
  --api-url "${API_URL_CONTAINER}" \
  --api-key "${API_KEY}" \
  --text "$TEXT" \
  --out-dir "$OUT_API"

echo ""
echo "Benchmark complete."
echo "  gRPC manifest: ${OUT_GRPC}/manifest.json (inside inference container)"
echo "  API manifest:  ${OUT_API}/manifest.json (inside inference container)"
