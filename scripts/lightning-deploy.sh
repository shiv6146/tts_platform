#!/usr/bin/env bash
# Run on Lightning GPU node after: git clone https://github.com/shiv6146/tts_platform.git
set -euo pipefail
cd "$(dirname "$0")/.."
BACKEND="${1:-llamacpp}"

git pull origin main
git submodule update --init --recursive
cp -n .env.example .env 2>/dev/null || true

chmod +x scripts/compose-up.sh scripts/bench_rtf.sh scripts/e2e_smoke.sh
./scripts/compose-up.sh "$BACKEND"

API_KEY=$(docker compose logs api 2>&1 | sed -n 's/.*default API key (save now): \(sk-[^ ]*\).*/\1/p' | tail -1 || true)
export API_KEY
./scripts/e2e_smoke.sh

echo "Deploy OK (backend=$BACKEND). Benchmark: ./scripts/bench_rtf.sh"
