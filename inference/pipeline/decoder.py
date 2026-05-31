"""Sync PCM stream from token generators."""

from __future__ import annotations

import logging
from typing import Generator, Iterable

from . import long_text
from .speechpipe import ensure_snac_loaded, tokens_decoder_sync

log = logging.getLogger("inference.decoder")

SAMPLE_RATE = 24000
MAX_BATCH_CHARS = 1000


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
