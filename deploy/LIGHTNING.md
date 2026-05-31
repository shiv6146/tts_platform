# Lightning.ai GPU deployment

## Prerequisites

- NVIDIA GPU node with Docker + NVIDIA Container Toolkit
- ~15GB disk for HF model cache
- Public clone: https://github.com/shiv6146/tts_platform

## Deploy

Work in the persistent studio directory (files outside it are not saved to Drive):

```bash
cd /teamspace/studios/this_studio
git clone https://github.com/shiv6146/tts_platform.git
cd tts_platform
git submodule update --init --recursive
cp .env.example .env
# Add HF_TOKEN=hf_... to .env (required for gated Orpheus model)
# Ensure INFERENCE_MOCK=false and INFERENCE_BACKEND=vllm

docker compose -f docker-compose.yml -f docker-compose.gpu.yml up --build -d
docker compose logs -f inference
```

First boot downloads `canopylabs/orpheus-3b-0.1-ft` (10–20 minutes). Inference `Health` returns `ok=true` only after vLLM + SNAC are ready.

## Verify

```bash
chmod +x scripts/e2e_smoke.sh
./scripts/e2e_smoke.sh
```

## GPU notes

| GPU | Typical settings |
|-----|------------------|
| T4 (16GB) | `VLLM_GPU_MEMORY_UTILIZATION=0.75`, `VLLM_MAX_MODEL_LEN=8192`, float16 (auto) |
| L4 / A10G (24GB) | `VLLM_GPU_MEMORY_UTILIZATION=0.85`, omit `VLLM_MAX_MODEL_LEN` for auto 16384, bfloat16 (auto) |

Stream/live need inference **RTF &lt; 1** (chunks faster than 85ms playback). Benchmark:

```bash
docker compose exec inference python /app/scripts/debug_pcm_stream.py --grpc \
  --grpc-addr 127.0.0.1:50051 --out-dir /tmp/debug_pcm
```

Target: `inter_chunk_gap_ms_avg` &lt; 85.

## Troubleshooting

| Symptom | Action |
|---------|--------|
| CUDA OOM | Lower `VLLM_GPU_MEMORY_UTILIZATION` to `0.75` in `.env` |
| Stream/live stutter, async OK | GPU too slow (RTF&gt;1); upgrade GPU or use async |
| Health never OK | `docker compose logs inference` — wait for model load |
| Build fails on vLLM | Confirm CUDA 12.4+ driver; rebuild `Dockerfile.gpu` |
| Empty/small audio | Confirm `INFERENCE_MOCK=false`; check inference errors |

## Update

```bash
git pull origin main
git submodule update --init --recursive
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up --build -d
```
