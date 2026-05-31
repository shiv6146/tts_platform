"""Token stream via Orpheus-TTS OrpheusModel (vLLM)."""

from __future__ import annotations

import logging
import os
import sys
import threading
from typing import Generator, Optional

log = logging.getLogger("inference.vllm")

_model = None
_load_error: Optional[str] = None
_lock = threading.Lock()
_load_started = False

CHUNK_AUDIO_SEC = 2048 / 24000  # SNAC slice per yield (~85.3ms)


def _gpu_profile() -> str:
    return os.environ.get("INFERENCE_GPU_PROFILE", "l4").strip().lower()


def _engine_kwargs() -> dict:
    import torch

    device = os.environ.get("VLLM_DEVICE", "cuda").lower()
    kw: dict = {}
    if device == "cpu":
        os.environ.setdefault("VLLM_TARGET_DEVICE", "cpu")
        kw["device"] = "cpu"
        kw["enforce_eager"] = True
    profile = _gpu_profile()
    gpu_util = os.environ.get("VLLM_GPU_MEMORY_UTILIZATION")
    if gpu_util:
        kw["gpu_memory_utilization"] = float(gpu_util)
    elif profile == "t4":
        kw["gpu_memory_utilization"] = 0.75
    if os.environ.get("VLLM_MAX_MODEL_LEN"):
        kw["max_model_len"] = int(os.environ["VLLM_MAX_MODEL_LEN"])
    elif profile == "t4":
        kw["max_model_len"] = 8192
        log.info("vLLM max_model_len=8192 (INFERENCE_GPU_PROFILE=t4)")
    elif device != "cpu" and torch.cuda.is_available():
        vram_gb = torch.cuda.get_device_properties(0).total_memory / (1024**3)
        if vram_gb < 18:
            kw["max_model_len"] = 8192
        elif vram_gb < 40:
            kw["max_model_len"] = 16384
        else:
            kw["max_model_len"] = 32768
        log.info("vLLM max_model_len=%s (%.1fGB VRAM, profile=%s)", kw["max_model_len"], vram_gb, profile)
    return kw


def _model_dtype():
    import torch

    if _gpu_profile() == "t4":
        log.info("INFERENCE_GPU_PROFILE=t4: using float16 (T4-equivalent dtype)")
        return torch.float16

    override = os.environ.get("VLLM_DTYPE", "").lower()
    if override in ("float16", "half", "fp16"):
        return torch.float16
    if override in ("bfloat16", "bf16"):
        return torch.bfloat16
    if override in ("float32", "fp32"):
        return torch.float32
    if os.environ.get("VLLM_DEVICE", "cuda").lower() == "cpu":
        return torch.float32
    if torch.cuda.is_available():
        major, minor = torch.cuda.get_device_capability(0)
        if major < 8:
            log.info(
                "GPU compute capability %s.%s — using float16 (bfloat16 needs >= 8.0)",
                major,
                minor,
            )
            return torch.float16
    return torch.bfloat16


def _load():
    global _model, _load_error
    try:
        vendor = os.environ.get("ORPHEUS_VENDOR", "/app/vendor/Orpheus-TTS/orpheus_tts_pypi")
        if vendor not in sys.path:
            sys.path.insert(0, vendor)
        from orpheus_tts import OrpheusModel  # type: ignore

        model_name = os.environ.get(
            "ORPHEUS_MODEL_NAME",
            "canopylabs/orpheus-3b-0.1-ft",
        )
        tokenizer = os.environ.get(
            "ORPHEUS_TOKENIZER",
            "canopylabs/orpheus-3b-0.1-pretrained",
        )
        log.info("loading vLLM OrpheusModel %s", model_name)
        with _lock:
            _model = OrpheusModel(
                model_name=model_name,
                dtype=_model_dtype(),
                tokenizer=tokenizer,
                **_engine_kwargs(),
            )
        log.info("vLLM OrpheusModel ready")
    except Exception as exc:
        _load_error = str(exc)
        log.exception("vLLM load failed")


def start_load() -> None:
    global _load_started
    with _lock:
        if _load_started:
            return
        _load_started = True
    threading.Thread(target=_load, name="vllm-load", daemon=True).start()


def model_ready() -> bool:
    return _model is not None


def make_token_generator(
    prompt: str,
    voice: str,
    *,
    request_id: str = "",
) -> Generator[str, None, None]:
    if _model is None:
        if _load_error:
            raise RuntimeError(f"vLLM model not loaded: {_load_error}")
        raise RuntimeError("vLLM model still loading")
    rid = request_id or "req-001"
    yield from _model.generate_tokens_sync(
        prompt=prompt,
        voice=voice,
        request_id=rid,
        repetition_penalty=1.1,
    )
