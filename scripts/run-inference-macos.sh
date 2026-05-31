#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

VENV="${ROOT}/.venv-inference"
if [[ ! -d "$VENV" ]]; then
  echo "Run ./scripts/setup-inference-macos.sh first"
  exit 1
fi
# shellcheck disable=SC1091
source "$VENV/bin/activate"

export INFERENCE_MOCK="${INFERENCE_MOCK:-false}"
export ORPHEUS_MODEL_NAME="${ORPHEUS_MODEL_NAME:-canopylabs/orpheus-3b-0.1-ft}"
export ORPHEUS_TOKENIZER="${ORPHEUS_TOKENIZER:-canopylabs/orpheus-3b-0.1-pretrained}"
export VLLM_DEVICE="${VLLM_DEVICE:-cpu}"
export VLLM_TARGET_DEVICE=cpu
export ORPHEUS_VENDOR="${ROOT}/vendor/Orpheus-TTS/orpheus_tts_pypi"
export PYTHONPATH="${ROOT}/inference"
export GRPC_PORT="${GRPC_PORT:-50051}"

exec python "${ROOT}/inference/server.py"
