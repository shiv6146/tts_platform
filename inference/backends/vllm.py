"""Token stream via Orpheus-TTS OrpheusModel (vLLM)."""

from __future__ import annotations

import asyncio
import logging
import os
import queue
import sys
import threading
import uuid
from typing import Generator, Optional

log = logging.getLogger("inference.vllm")

_model = None
_load_error: Optional[str] = None
_lock = threading.Lock()
_load_started = False

# Single persistent event loop drives the shared AsyncLLMEngine so concurrent
# requests are continuously batched. The vendor generate_tokens_sync() spawns a
# fresh asyncio.run() loop per call, which deadlocks the engine under concurrency.
_loop: Optional[asyncio.AbstractEventLoop] = None
STOP_TOKEN_IDS = [49158]  # Orpheus audio end token (verified: stops at end of speech)


def _ensure_loop() -> asyncio.AbstractEventLoop:
    global _loop
    if _loop is not None:
        return _loop
    with _lock:
        if _loop is None:
            loop = asyncio.new_event_loop()
            threading.Thread(target=loop.run_forever, name="vllm-loop", daemon=True).start()
            _loop = loop
    return _loop

CHUNK_AUDIO_SEC = 2048 / 24000  # SNAC slice per yield (~85.3ms)

REPETITION_PENALTY = 1.1


def _gpu_profile() -> str:
    return os.environ.get("INFERENCE_GPU_PROFILE", "l4").strip().lower()


def _max_tokens() -> int:
    # Match llama.cpp per-request budget; vLLM paged KV is per-request (not shared),
    # so concurrency does not shrink this. Vendor default (1200) truncates our batches.
    return int(os.environ.get("ORPHEUS_MAX_TOKENS", "4096"))


def _temperature() -> float:
    return float(os.environ.get("ORPHEUS_TEMPERATURE", "0.6"))


def _top_p() -> float:
    return float(os.environ.get("ORPHEUS_TOP_P", "0.9"))


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
    from vllm import SamplingParams

    rid = request_id or f"req-{uuid.uuid4().hex[:12]}"
    engine = _model.engine
    prompt_string = _model._format_prompt(prompt, voice)
    sampling = SamplingParams(
        temperature=_temperature(),
        top_p=_top_p(),
        max_tokens=_max_tokens(),
        stop_token_ids=STOP_TOKEN_IDS,
        repetition_penalty=REPETITION_PENALTY,
    )

    loop = _ensure_loop()
    q: "queue.Queue" = queue.Queue(maxsize=512)
    sentinel = object()

    async def _produce() -> None:
        prev = ""
        try:
            async for out in engine.generate(
                prompt=prompt_string, sampling_params=sampling, request_id=rid
            ):
                text = out.outputs[0].text
                delta = text[len(prev):]
                prev = text
                if delta:
                    q.put(delta)
        except Exception as exc:  # propagate to consumer thread
            q.put(exc)
        finally:
            q.put(sentinel)

    fut = asyncio.run_coroutine_threadsafe(_produce(), loop)

    def _gen() -> Generator[str, None, None]:
        try:
            while True:
                item = q.get()
                if item is sentinel:
                    break
                if isinstance(item, BaseException):
                    raise item
                # Split into individual <custom_token_*> like the llama.cpp path so
                # the SNAC decoder never sees two audio tokens fused in one yield.
                for piece in item.split(">"):
                    tok = f"{piece}>"
                    if tok and tok != ">":
                        yield tok
        finally:
            if not fut.done():
                loop.call_soon_threadsafe(fut.cancel)

    return _gen()
