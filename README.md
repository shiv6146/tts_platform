# Orpheus TTS Platform

Go API gateway + metering, Python gRPC inference (Orpheus 3B), Postgres, Valkey, NATS.

**Model (GPU cloud):** `canopylabs/orpheus-3b-0.1-ft` via vLLM  
**Model (macOS):** `Orpheus-3b-FT-Q8_0.gguf` via llama.cpp

## Lightning GPU (production)

See [deploy/LIGHTNING.md](deploy/LIGHTNING.md).

```bash
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up --build -d
./scripts/e2e_smoke.sh
```

`INFERENCE_MOCK=false` always; stack uses optimized SNAC pipeline (7-token first chunk) from Orpheus-FastAPI.

## Apple Silicon (local)

```bash
docker compose -f docker-compose.yml -f docker-compose.macos.yml up --build
```

Runs llama.cpp + GGUF; inference gRPC uses `INFERENCE_BACKEND=llamacpp`.

## Architecture

- **Token backends:** auto — Darwin/arm64 → llama.cpp; CUDA Linux → vLLM
- **Shared:** `speechpipe` SNAC decode, streaming PCM over gRPC (`proto/tts/v1/inference.proto`)
- **Go API:** unchanged OpenAPI + WS; talks to inference on `:50051`

## Codegen

```bash
make gen-api
make gen-proto-py
```

## Submodule

```bash
git submodule update --init --recursive   # vendor/Orpheus-TTS for vLLM
```
