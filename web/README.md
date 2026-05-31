# TTS Platform Web UI

Demo SPA for the Orpheus TTS platform (auth, wallet, usage, Voice Lab / Stream / Live).

## Development

```bash
# API on :8080
cd ../ && docker compose up -d api inference postgres valkey nats

cd web
bun install
bun run dev
```

Open http://localhost:5173 — Vite proxies `/v1` to the API.

## Build (embedded in Go API)

```bash
bun run build
# or from repo root:
make build-web
```

Output is copied to `api/internal/ui/dist` and served at `/` by the API container.

## Features

- **Voice Lab** — pick voice, emotive tag chips, async WAV generation
- **Stream** — chunked PCM playback
- **Live** — WebSocket with debounced phrases; auth via `tts_token` cookie
- **Usage** — billing events with audio seconds and cost
