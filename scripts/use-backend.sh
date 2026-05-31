#!/usr/bin/env bash
# Switch GPU inference stack (mutually exclusive). Stops orphan llama-cpp when moving to vLLM.
set -euo pipefail
cd "$(dirname "$0")/.."
BACKEND="${1:-}"

if [[ "$BACKEND" != "llamacpp" && "$BACKEND" != "vllm" ]]; then
  echo "Usage: $0 llamacpp|vllm" >&2
  exit 1
fi

case "$BACKEND" in
  llamacpp)
    export COMPOSE_FILE=docker-compose.yml:docker-compose.llamacpp-gpu.yml
    ;;
  vllm)
    export COMPOSE_FILE=docker-compose.yml:docker-compose.gpu.yml
    ;;
esac

echo "COMPOSE_FILE=$COMPOSE_FILE"
echo "Stopping previous GPU sidecars (if any)..."
docker compose stop llama-cpp-server model-init 2>/dev/null || true
docker compose rm -f llama-cpp-server model-init 2>/dev/null || true

echo "Recreating inference + api..."
docker compose up -d --build --force-recreate inference api

echo "Waiting for inference health..."
for i in $(seq 1 60); do
  if docker compose exec -T inference python -c "
import grpc,sys
sys.path.insert(0,'/app/inference')
from tts.v1 import inference_pb2,inference_pb2_grpc
r=inference_pb2_grpc.TTSInferenceStub(grpc.insecure_channel('127.0.0.1:50051')).Health(inference_pb2.HealthRequest(),timeout=60)
print('ok=', r.ok, 'backend=', getattr(r, 'backend', ''))
exit(0 if r.ok else 1)" 2>/dev/null; then
    break
  fi
  sleep 10
done

curl -sf http://127.0.0.1:8080/v1/meta/inference | python3 -m json.tool || true
docker compose ps inference llama-cpp-server api 2>/dev/null || docker compose ps inference api
