#!/usr/bin/env bash
# Bring up full tts_platform stack for a GPU inference backend (default: llamacpp + llama-cpp-server).
set -euo pipefail
cd "$(dirname "$0")/.."

BACKEND="${1:-llamacpp}"
BUILD="${COMPOSE_BUILD:-1}"

set_compose_file() {
  local b
  b="$(echo "$BACKEND" | tr '[:upper:]' '[:lower:]')"
  case "$b" in
    llamacpp|llama-cpp|llama|gguf)
      export COMPOSE_FILE=docker-compose.yml:docker-compose.llamacpp-gpu.yml
      BACKEND=llamacpp
      ;;
    vllm|gpu|hf)
      export COMPOSE_FILE=docker-compose.yml:docker-compose.gpu.yml
      BACKEND=vllm
      ;;
    *)
      echo "Usage: $0 [llamacpp|vllm]" >&2
      echo "  llamacpp (default) — GGUF + llama-cpp-server + inference gRPC" >&2
      echo "  vllm             — in-process vLLM inference (no llama-cpp-server)" >&2
      exit 1
      ;;
  esac
}

wait_inference() {
  echo "Waiting for inference gRPC health..."
  for i in $(seq 1 90); do
    if docker compose exec -T inference python -c "
import grpc,sys
sys.path.insert(0,'/app/inference')
from tts.v1 import inference_pb2,inference_pb2_grpc
r=inference_pb2_grpc.TTSInferenceStub(
    grpc.insecure_channel('127.0.0.1:50051')
).Health(inference_pb2.HealthRequest(), timeout=60)
print('ok', r.ok, 'backend', getattr(r, 'backend', ''))
sys.exit(0 if r.ok else 1)" 2>/dev/null; then
      return 0
    fi
    sleep 10
  done
  echo "Inference not healthy — docker compose logs inference" >&2
  return 1
}

wait_api() {
  echo "Waiting for API http://127.0.0.1:8080/health ..."
  for i in $(seq 1 30); do
    if curl -sf "http://127.0.0.1:8080/health" >/dev/null 2>&1; then
      return 0
    fi
    sleep 3
  done
  echo "API not reachable on :8080" >&2
  return 1
}

set_compose_file
echo "COMPOSE_FILE=$COMPOSE_FILE"
echo "Inference backend: $BACKEND"

if [[ "$BACKEND" == "vllm" ]]; then
  docker compose stop llama-cpp-server model-init 2>/dev/null || true
  docker compose rm -f llama-cpp-server model-init 2>/dev/null || true
fi

LLAMACPP_REPLICAS="${LLAMACPP_REPLICAS:-1}"
UP_ARGS=(up -d)
[[ "$BUILD" == "1" ]] && UP_ARGS=(up -d --build)
if [[ "$BACKEND" == "llamacpp" && "$LLAMACPP_REPLICAS" =~ ^[0-9]+$ && "$LLAMACPP_REPLICAS" -gt 0 ]]; then
  echo "llama-cpp-server replicas: $LLAMACPP_REPLICAS (via llama-lb nginx)"
  UP_ARGS+=(--scale "llama-cpp-server=${LLAMACPP_REPLICAS}")
fi
docker compose "${UP_ARGS[@]}"

if [[ "$BACKEND" == "llamacpp" ]]; then
  echo "Waiting for llama-cpp-server replicas (model load)..."
  for i in $(seq 1 40); do
    healthy=0
    for cid in $(docker compose ps -q llama-cpp-server 2>/dev/null); do
      st=$(docker inspect -f '{{.State.Health.Status}}' "$cid" 2>/dev/null || echo starting)
      echo "  $(docker inspect -f '{{.Name}}' "$cid" | sed 's#^/##') health=$st"
      [[ "$st" == "healthy" ]] && healthy=$((healthy + 1))
    done
    want="${LLAMACPP_REPLICAS:-1}"
    [[ "$healthy" -ge "$want" ]] && break
    sleep 15
  done
  echo "Waiting for llama-lb..."
  for i in $(seq 1 20); do
    st=$(docker inspect -f '{{.State.Health.Status}}' tts_platform-llama-lb-1 2>/dev/null || echo starting)
    echo "  llama-lb health=$st"
    [[ "$st" == "healthy" ]] && break
    sleep 5
  done
fi

wait_inference
wait_api

echo ""
docker compose ps
echo ""
curl -sf "http://127.0.0.1:8080/v1/meta/inference" 2>/dev/null | python3 -m json.tool || true
echo ""
echo "Stack ready. API :8080 | Grafana :3000 | backend=$BACKEND"
