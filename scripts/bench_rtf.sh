#!/usr/bin/env bash
# Benchmark gRPC PCM streaming RTF (requires inference healthy).
set -euo pipefail
cd "$(dirname "$0")/.."
export COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.yml:docker-compose.llamacpp-gpu.yml}"
TAG="${1:-llamacpp_gpu}"
TEXT="${BENCH_TEXT:-Hello, this is a streaming debug test for Orpheus TTS.}"

docker compose exec -T inference mkdir -p /app/scripts
docker compose cp scripts/debug_pcm_stream.py inference:/app/scripts/debug_pcm_stream.py
echo "Backend: ${INFERENCE_BACKEND:-llamacpp} | GGUF: ${ORPHEUS_GGUF_MODEL:-Orpheus-3b-FT-Q8_0.gguf}"
docker compose exec -T inference python /app/scripts/debug_pcm_stream.py --grpc \
  --grpc-addr 127.0.0.1:50051 \
  --text "$TEXT" \
  --out-dir "/tmp/bench_${TAG}"
