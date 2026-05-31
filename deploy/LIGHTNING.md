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
# Add HF_TOKEN=hf_... to .env if needed for gated downloads
# Ensure INFERENCE_MOCK=false

# vLLM + HF FT (default)
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up --build -d

# GGUF + llama.cpp CUDA (RTF experiment; lex-au checkpoints)
docker compose -f docker-compose.yml -f docker-compose.llamacpp-gpu.yml up --build -d

docker compose logs -f inference
```

vLLM first boot downloads `canopylabs/orpheus-3b-0.1-ft` (10–20 minutes).  
llama.cpp first boot downloads `lex-au/Orpheus-3b-FT-Q8_0.gguf` into volume `gguf_models` (several GB).

Optional faster quant:

```bash
ORPHEUS_GGUF_MODEL=Orpheus-3b-FT-Q4_K_M.gguf
ORPHEUS_GGUF_HF_REPO=lex-au/Orpheus-3b-FT-Q4_K_M.gguf
```

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

### llama.cpp GGUF benchmark

```bash
export COMPOSE_FILE=docker-compose.yml:docker-compose.llamacpp-gpu.yml
chmod +x scripts/bench_rtf.sh
./scripts/bench_rtf.sh llamacpp_q8
```

## Monitoring (Grafana / Prometheus)

Both are defined in `docker-compose.yml`. Start the **full** stack (not only `api` / `inference`):

```bash
export COMPOSE_FILE=docker-compose.yml:docker-compose.llamacpp-gpu.yml
docker compose up -d
docker compose ps grafana prometheus metering
```

| URL | Default login |
|-----|----------------|
| Grafana `http://<host>:3000` | `admin` / `admin` |
| Prometheus | internal `prometheus:9090` only (expose manually if needed; 9090/9091 often taken) |
| API metrics | `http://<host>:8080/metrics` |

Provisioned dashboard: **TTS Platform** (wallet, usage, inference latency). Prometheus scrapes `api:8080` and `metering:8081` on the compose network.

`llama-cpp-server` may show **unhealthy** on old images: built-in healthcheck hits port **8080** while the server listens on **5006**. Current `docker-compose.llamacpp-gpu.yml` overrides this with `curl http://127.0.0.1:5006/health`.

## Troubleshooting

| Symptom | Action |
|---------|--------|
| CUDA OOM | Lower `VLLM_GPU_MEMORY_UTILIZATION` to `0.75` in `.env` |
| Stream/live stutter, async OK | GPU too slow (RTF&gt;1); upgrade GPU or use async |
| Health never OK | `docker compose logs inference` — wait for model load |
| `llama-cpp-server` unhealthy | `curl -f http://127.0.0.1:5006/health` inside container; recreate after compose healthcheck fix |
| No Grafana on :3000 | Run `docker compose up -d` (all services); check `docker compose ps grafana` |
| Build fails on vLLM | Confirm CUDA 12.4+ driver; rebuild `Dockerfile.gpu` |
| Empty/small audio | Confirm `INFERENCE_MOCK=false`; check inference errors |

## Update

```bash
git pull origin main
git submodule update --init --recursive
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up --build -d
```
