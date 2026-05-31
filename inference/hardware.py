"""Hardware detection and tuning (ported from Orpheus-FastAPI)."""

from __future__ import annotations

import logging
import os

import psutil
import torch

log = logging.getLogger("inference.hardware")

HIGH_END_GPU = False
NUM_WORKERS = 2
DECODER_QUEUE_SIZE = 50
TOKEN_BATCH_SIZE = 16

SAMPLE_RATE = int(os.environ.get("ORPHEUS_SAMPLE_RATE", "24000"))


def _env_bool(name: str, default: bool = False) -> bool:
    return os.environ.get(name, str(default)).lower() in ("1", "true", "yes")


def gpu_profile() -> str:
    """t4 = 16GB-era caps; l4 = 24GB+ tuning. Use t4 on L4 for apples-to-apples benchmarks."""
    return os.environ.get("INFERENCE_GPU_PROFILE", "l4").strip().lower()


def init_hardware() -> str:
    """Detect hardware, set module-level tuning knobs, return summary string."""
    global HIGH_END_GPU, NUM_WORKERS, DECODER_QUEUE_SIZE, TOKEN_BATCH_SIZE

    backend = os.environ.get("INFERENCE_BACKEND", "auto").lower()
    vllm_colocated = backend == "vllm" or (
        backend == "auto" and torch.cuda.is_available()
    )

    if torch.cuda.is_available():
        props = torch.cuda.get_device_properties(0)
        gpu_name = props.name
        gpu_mem_gb = props.total_memory / (1024**3)
        HIGH_END_GPU = gpu_mem_gb >= 16.0 or props.major >= 8 or (
            gpu_mem_gb >= 12.0 and props.major >= 7
        )
        if HIGH_END_GPU:
            NUM_WORKERS = 4
            DECODER_QUEUE_SIZE = 100
            TOKEN_BATCH_SIZE = 32
            log.info(
                "high-end CUDA GPU: %s %.1fGB CC %s.%s",
                gpu_name,
                gpu_mem_gb,
                props.major,
                props.minor,
            )
        else:
            NUM_WORKERS = 2
            DECODER_QUEUE_SIZE = 50
            TOKEN_BATCH_SIZE = 16
            log.info("CUDA GPU: %s %.1fGB", gpu_name, gpu_mem_gb)

        if vllm_colocated:
            profile = gpu_profile()
            if profile == "t4" or gpu_mem_gb < 20:
                NUM_WORKERS = max(1, NUM_WORKERS // 2)
                DECODER_QUEUE_SIZE = min(DECODER_QUEUE_SIZE, 32)
                TOKEN_BATCH_SIZE = min(TOKEN_BATCH_SIZE, 8)
            else:
                DECODER_QUEUE_SIZE = min(DECODER_QUEUE_SIZE, 64)
                TOKEN_BATCH_SIZE = min(TOKEN_BATCH_SIZE, 24)
            log.info(
                "vLLM colocated profile=%s: workers=%s queue=%s batch=%s (%.1fGB VRAM)",
                profile,
                NUM_WORKERS,
                DECODER_QUEUE_SIZE,
                TOKEN_BATCH_SIZE,
                gpu_mem_gb,
            )
        return f"cuda:{gpu_name}"

    cpu_cores = psutil.cpu_count(logical=False) or 1
    cpu_threads = psutil.cpu_count(logical=True) or cpu_cores
    ram_gb = psutil.virtual_memory().total / (1024**3)
    NUM_WORKERS = 2
    DECODER_QUEUE_SIZE = 50
    TOKEN_BATCH_SIZE = 16
    log.info("CPU only: cores=%s threads=%s ram=%.1fGB", cpu_cores, cpu_threads, ram_gb)
    return f"cpu:{cpu_threads}t"
