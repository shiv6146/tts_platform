#!/usr/bin/env bash
# End-to-end smoke test against running tts_platform stack.
set -euo pipefail

API_URL="${API_URL:-http://localhost:8080}"
USER="${DEFAULT_USERNAME:-dev}"
PASS="${DEFAULT_PASSWORD:-devpassword}"

echo "==> API health"
curl -sf "${API_URL}/health" | head -c 200
echo

if [[ -z "${API_KEY:-}" ]]; then
  API_KEY=$(docker compose logs api 2>/dev/null | sed -n 's/.*default API key (save now): \(sk-[^ ]*\).*/\1/p' | tail -1 || true)
fi
if [[ -z "${API_KEY:-}" ]]; then
  echo "Set API_KEY from first api boot log: default API key (save now): sk-..."
  exit 1
fi
echo "Using API key (${#API_KEY} chars)"

echo "==> TTS stream"
OUT=$(mktemp)
HTTP=$(curl -s -o "$OUT" -w "%{http_code}" -X POST "${API_URL}/v1/tts/stream" \
  -H "Authorization: Bearer ${API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"text":"Hello from e2e smoke test.","voice":"tara"}')
SIZE=$(wc -c <"$OUT" | tr -d ' ')
echo "HTTP $HTTP body_bytes=$SIZE"
if [[ "$HTTP" != "200" ]]; then
  cat "$OUT"
  exit 1
fi
if [[ "$SIZE" -lt 1000 ]]; then
  echo "PCM too small — inference may still be mock or failed"
  exit 1
fi
rm -f "$OUT"
echo "==> OK"
