"""Sync PCM stream from token generators."""

from __future__ import annotations

import logging
import os
from typing import Generator, Iterable

from . import long_text
from .speechpipe import ensure_snac_loaded, tokens_decoder_sync

log = logging.getLogger("inference.decoder")

SAMPLE_RATE = 24000

# Orpheus emits ~6.5 audio tokens per spoken character (~82 tokens/s of audio).
# A batch must render within the per-request token budget (ORPHEUS_MAX_TOKENS),
# which itself must fit the llama.cpp per-slot context (ctx_size / parallel).
TOKENS_PER_CHAR = 6.5


def _max_batch_chars() -> int:
    explicit = os.environ.get("ORPHEUS_BATCH_CHARS", "").strip()
    if explicit:
        return max(80, int(explicit))
    budget = int(os.environ.get("ORPHEUS_MAX_TOKENS", "4096"))
    # 30% headroom so a batch never hits the generation cap mid-sentence.
    return max(200, min(1000, int(budget / TOKENS_PER_CHAR * 0.7)))


MAX_BATCH_CHARS = _max_batch_chars()


def stream_pcm_from_tokens(token_gen: Iterable[str]) -> Generator[bytes, None, None]:
    ensure_snac_loaded()
    yield from tokens_decoder_sync(iter(token_gen))


def stream_pcm_for_text(
    text: str,
    token_gen_factory,
    *,
    voice: str = "tara",
    max_batch_chars: int = MAX_BATCH_CHARS,
) -> Generator[bytes, None, None]:
    """token_gen_factory(prompt, voice) -> iterable of token strings."""
    ensure_snac_loaded()
    if len(text) <= max_batch_chars:
        yield from stream_pcm_from_tokens(token_gen_factory(text, voice))
        return

    log.info("long text batching: %d chars", len(text))
    batches = long_text.make_batches(text, max_batch_chars)
    segments: list[bytes] = []
    for batch in batches:
        segments.append(b"".join(stream_pcm_from_tokens(token_gen_factory(batch, voice))))
    stitched = long_text.stitch_pcm_segments(segments, SAMPLE_RATE)
    chunk_size = 8192
    for i in range(0, len(stitched), chunk_size):
        yield stitched[i : i + chunk_size]
