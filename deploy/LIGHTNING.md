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

# Full stack (default: llamacpp + llama-cpp-server + api + grafana + prometheus)
./scripts/compose-up.sh              # same as: compose-up.sh llamacpp
./scripts/compose-up.sh vllm         # vLLM in-process, no llama-cpp-server

# Switch backend (alias for compose-up)
./scripts/use-backend.sh llamacpp

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
./scripts/compose-up.sh llamacpp
./scripts/bench_rtf.sh l40s_q8    # on-studio: gRPC then API

# From your laptop against the public API URL:
export API_URL=https://<your-studio-host>:8080
./scripts/bench_remote.sh stream 8 1,4,8
./scripts/bench_remote.sh all 12 1,4,8
```

### L40S production profile (recommended)

Single tuned **Q4** `llama-server` + **3× inference** gRPC replicas (SNAC parallelism):

```bash
# .env
ORPHEUS_GGUF_MODEL=Orpheus-3b-FT-Q4_K_M.gguf
ORPHEUS_GGUF_HF_REPO=lex-au/Orpheus-3b-FT-Q4_K_M.gguf
LLAMACPP_REPLICAS=1
LLAMACPP_PARALLEL=48
LLAMACPP_CTX_SIZE=8192   # 4096 truncates long lines (~50s audio cap); Q4 is fine at 8192 on L40S
INFERENCE_REPLICAS=3
INFERENCE_GRPC_ADDR=dns:///inference:50051
MAX_CONCURRENT_SYNTHESIS=48
RATE_LIMIT_RPM=5000   # bench only

./scripts/compose-up.sh llamacpp
```

API uses gRPC **round_robin** across scaled `inference` containers. Each replica has its own GIL + per-thread CUDA SNAC streams.

### Multiple llama.cpp replicas (one L40S, keep Q8)

Docker does not auto-balance HTTP by itself. This stack uses **nginx (`llama-lb`)** with `least_conn` over scaled `llama-cpp-server` containers (Docker DNS resolves all replicas).

```bash
# In .env — e.g. 2 replicas × 8 parallel slots ≈ 16 concurrent sequences, ~10GB weights
LLAMACPP_REPLICAS=2
LLAMACPP_PARALLEL=8
MAX_CONCURRENT_SYNTHESIS=16

./scripts/compose-up.sh llamacpp
```

Each replica loads the full GGUF on the **same GPU** (shared VRAM, shared SM). Useful when one process saturates; diminishing returns past ~2–3 replicas on a single L40S. Monitor `nvidia-smi` before `LLAMACPP_REPLICAS=3`.

### Concurrency (40 streams on L40S)

Remote bench (`bench_remote_out/remote/manifest.json`) shows **queueing**, not broken streaming:

| Metric | c=1 | c=4 |
|--------|-----|-----|
| TTFB p95 | ~1.2s | ~9.4s |
| Inter-chunk gap | ~51ms | ~51ms |
| RTF (streaming) | ~0.60 | ~0.60 |
| Throughput | ~1.16 audio-s/s | ~0.44 audio-s/s |

Once audio starts, each stream stays real-time (RTF &lt; 1). TTFB grows because **llama.cpp was `--parallel 1`** — only one GGUF sequence on GPU at a time.

Tune in `.env` (then recreate `llama-cpp-server`, `inference`, `api`):

```bash
LLAMACPP_PARALLEL=16          # llama.cpp concurrent slots (VRAM ↑ with parallel × ctx)
MAX_CONCURRENT_SYNTHESIS=16   # API admission; 503 when full (not 12s queue)
GRPC_MAX_WORKERS=48           # inference gRPC thread pool
```

For **40 concurrent** on one L40S:

1. **Raise parallel** toward 32–40; use **Q4** (`Orpheus-3b-FT-Q4_K_M.gguf`) if VRAM is tight at high `--parallel`.
2. Set `MAX_CONCURRENT_SYNTHESIS` ≈ `LLAMACPP_PARALLEL` so clients get fast **503** instead of silent queue.
3. Expect **RTF and gap to worsen** above ~8–16 simultaneous GPU decodes — physics on one GPU.
4. For **40 streams all with low TTFB**, scale out (multiple GPUs / llama replicas + load balancer) or use **vLLM continuous batching** (`./scripts/compose-up.sh vllm`).

```bash
export API_URL=https://<host>:8080
./scripts/bench_remote.sh stream 8 1,4,8,16,32,40
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

Only one GPU token backend at a time. The API always uses gRPC **`inference:50051`** (SNAC + backend). `llama-cpp-server` is an HTTP sidecar for GGUF tokens only — not a second gRPC target. Check routing: `curl -s http://localhost:8080/v1/meta/inference`.

`llama-cpp-server` may show **unhealthy** on old images: built-in healthcheck hits port **8080** while the server listens on **5006**. Current `docker-compose.llamacpp-gpu.yml` overrides this with `curl http://127.0.0.1:5006/health`.

## Troubleshooting

| Symptom | Action |
|---------|--------|
| CUDA OOM | Lower `VLLM_GPU_MEMORY_UTILIZATION` to `0.75` in `.env` |
| Stream/live stutter, async OK | GPU too slow (RTF&gt;1); upgrade GPU or use async |
| TTFB spikes at low concurrency | Raise `LLAMACPP_PARALLEL`; align `MAX_CONCURRENT_SYNTHESIS` |
| HTTP 503 synthesis capacity | Expected when at limit; raise parallel or scale out |
| Health never OK | `docker compose logs inference` — wait for model load |
| `llama-cpp-server` unhealthy | `curl -f http://127.0.0.1:5006/health` inside container; recreate after compose healthcheck fix |
| No Grafana on :3000 | Run `docker compose up -d` (all services); check `docker compose ps grafana` |
| Build fails on vLLM | Confirm CUDA 12.4+ driver; rebuild `Dockerfile.gpu` |
| Empty/small audio | Confirm `INFERENCE_MOCK=false`; check inference errors |
| Audio cuts off mid-text | Raise `LLAMACPP_CTX_SIZE` (8192+); not usually Q4 — ctx limits audio tokens (~80/s). Use `ORPHEUS_BATCH_CHARS=400` for long inputs |

## Update

```bash
git pull origin main
git submodule update --init --recursive
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up --build -d
```
