#!/usr/bin/env bash
# Run on Lightning GPU node after: git clone https://github.com/shiv6146/tts_platform.git
set -euo pipefail
cd "$(dirname "$0")/.."
git pull origin main
git submodule update --init --recursive
cp -n .env.example .env
export COMPOSE_FILE=docker-compose.yml:docker-compose.gpu.yml
docker compose build
docker compose up -d
echo "Waiting for inference (model download may take 15+ min)..."
for i in $(seq 1 60); do
  if docker compose exec -T inference python -c "
import grpc
from tts.v1 import inference_pb2, inference_pb2_grpc
ch = grpc.insecure_channel('localhost:50051')
stub = inference_pb2_grpc.TTSInferenceStub(ch)
print(stub.Health(inference_pb2.HealthRequest(), timeout=30).ok)
" 2>/dev/null | grep -q True; then
    echo "Inference healthy"
  API_KEY=$(docker compose logs api 2>&1 | sed -n 's/.*default API key (save now): \(sk-[^ ]*\).*/\1/p' | tail -1)
  export API_KEY
  ./scripts/e2e_smoke.sh
  exit 0
  fi
  sleep 30
done
echo "Timed out waiting for inference health — check: docker compose logs inference"
exit 1
