"""Select vLLM vs llama.cpp token backend."""

from __future__ import annotations

import logging
import os
import platform
from enum import Enum
from typing import Callable, Generator, Optional

log = logging.getLogger("inference.backends")

TokenGenFactory = Callable[[str, str], Generator[str, None, None]]

_backend_kind: Optional["BackendKind"] = None
_ready = False
_error: Optional[str] = None
_token_factory: Optional[TokenGenFactory] = None


class BackendKind(str, Enum):
    VLLM = "vllm"
    LLAMACPP = "llamacpp"


def resolve_backend_kind() -> BackendKind:
    explicit = os.environ.get("INFERENCE_BACKEND", "auto").lower()
    if explicit == "vllm":
        return BackendKind.VLLM
    if explicit in ("llamacpp", "llama.cpp", "llama"):
        return BackendKind.LLAMACPP
    if platform.system() == "Darwin" and platform.machine() == "arm64":
        log.info("auto backend: Darwin arm64 -> llamacpp")
        return BackendKind.LLAMACPP
    try:
        import torch

        if torch.cuda.is_available():
            log.info("auto backend: CUDA -> vllm")
            return BackendKind.VLLM
    except ImportError:
        pass
    log.info("auto backend: fallback -> llamacpp")
    return BackendKind.LLAMACPP


def init_backend() -> BackendKind:
    global _backend_kind, _ready, _error, _token_factory
    _backend_kind = resolve_backend_kind()
    try:
        if _backend_kind == BackendKind.VLLM:
            from . import vllm as vllm_be

            vllm_be.start_load()
            _token_factory = vllm_be.make_token_generator
        else:
            from . import llamacpp as llama_be

            llama_be.start_wait()
            _token_factory = llama_be.make_token_generator
        _ready = True
        _error = None
    except Exception as exc:
        _ready = False
        _error = str(exc)
        log.exception("backend init failed")
    return _backend_kind


def backend_ready() -> bool:
    if not _ready:
        return False
    if _backend_kind == BackendKind.VLLM:
        from . import vllm as vllm_be

        return vllm_be.model_ready()
    from . import llamacpp as llama_be

    return llama_be.server_ready()


def backend_error() -> Optional[str]:
    return _error


def get_token_generator() -> TokenGenFactory:
    if _token_factory is None:
        raise RuntimeError("backend not initialized")
    return _token_factory
