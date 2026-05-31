"""High-level synthesize: backend tokens -> optimized PCM stream."""

from __future__ import annotations

import logging
from typing import Generator

import hardware
from backends import backend_ready, get_token_generator, init_backend
from pipeline.decoder import stream_pcm_for_text
from pipeline.speechpipe import ensure_snac_loaded, snac_ready

log = logging.getLogger("inference.engine")
_initialized = False


def initialize() -> None:
    global _initialized
    if _initialized:
        return
    hardware.init_hardware()
    init_backend()
    ensure_snac_loaded()
    _initialized = True
    log.info("inference engine initialized")


def ready() -> bool:
    return backend_ready() and snac_ready()


def synthesize_pcm_stream(
    text: str,
    voice: str,
    request_id: str = "",
) -> Generator[bytes, None, None]:
    if not _initialized:
        initialize()
    factory = get_token_generator()

    def token_gen_factory(prompt: str, v: str):
        return factory(prompt, v, request_id=request_id)

    yield from stream_pcm_for_text(text, token_gen_factory, voice=voice or "tara")
