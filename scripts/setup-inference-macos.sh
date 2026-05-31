#!/usr/bin/env bash
# One-time venv for native Orpheus inference on Apple Silicon (CPU; vLLM macOS backend).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "This script is for macOS (Apple Silicon). On Linux use docker compose (CPU Dockerfile) or GPU overlay."
  exit 1
fi

if [[ ! -d vendor/Orpheus-TTS/orpheus_tts_pypi ]]; then
  echo "Missing vendor/Orpheus-TTS — run: git submodule update --init --recursive"
  exit 1
fi

VENV="${ROOT}/.venv-inference"
PYTHON="${PYTHON:-python3.11}"

if command -v uv >/dev/null 2>&1; then
  uv venv "$VENV" --python "$PYTHON" --seed
else
  "$PYTHON" -m venv "$VENV"
  # shellcheck disable=SC1091
  source "$VENV/bin/activate"
  python -m ensurepip --upgrade 2>/dev/null || {
    curl -sS https://bootstrap.pypa.io/get-pip.py | python
  }
fi

# shellcheck disable=SC1091
source "$VENV/bin/activate"
pip install --upgrade pip wheel

if command -v uv >/dev/null 2>&1; then
  uv pip install -r inference/requirements.txt
  make gen-proto-py
  uv pip install "cmake>=3.26" ninja
  uv pip install torch==2.5.1 torchaudio==2.5.1 \
    --index-url https://download.pytorch.org/whl/cpu
  UV_INDEX_STRATEGY=unsafe-best-match uv pip install -r inference/requirements-cpu.txt
else
  pip install -r inference/requirements.txt
  make gen-proto-py
  pip install "cmake>=3.26" ninja
  pip install torch==2.5.1 torchaudio==2.5.1 \
    --index-url https://download.pytorch.org/whl/cpu
  pip install -r inference/requirements-cpu.txt
fi

echo "Done. Activate with: source .venv-inference/bin/activate"
echo "Then: ./scripts/run-inference-macos.sh"
