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

## GPU profiles (`INFERENCE_GPU_PROFILE`)

| Profile | Use on | Effect |
|---------|--------|--------|
| `t4` | T4 16GB, or **L4 benchmark baseline** | `float16`, `max_model_len=8192`, `gpu_mem=0.75`, SNAC batch=8 |
| `l4` | L4 / A10G 24GB+ | `bfloat16` (CC≥8), auto `max_model_len` by VRAM, SNAC batch=24 |

Compare T4 settings on L4 hardware:

```bash
# In .env: INFERENCE_GPU_PROFILE=t4  then restart inference
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d inference
docker compose exec inference python /app/scripts/debug_pcm_stream.py --grpc \
  --grpc-addr 127.0.0.1:50051 --out-dir /tmp/bench_t4profile
# Switch to INFERENCE_GPU_PROFILE=l4 and VLLM_GPU_MEMORY_UTILIZATION=0.85, rerun
```

Stream/live need **RTF &lt; 1**: `inter_chunk_gap_ms_avg` &lt; 85 (~85ms per SNAC chunk).

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
