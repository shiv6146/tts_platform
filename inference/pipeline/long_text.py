"""Long-text sentence batching and PCM crossfade stitching."""

from __future__ import annotations

import struct
from typing import Generator, Iterable, List


def split_text_into_sentences(text: str) -> List[str]:
    parts: List[str] = []
    current = ""
    for char in text:
        current += char
        if char in (" ", "\n", "\t") and len(current) > 1:
            prev_char = current[-2]
            if prev_char in (".", "!", "?"):
                if len(current) > 3 and current[-3] not in (".", " "):
                    parts.append(current.strip())
                    current = ""
    if current.strip():
        parts.append(current.strip())

    min_chars = 20
    combined: List[str] = []
    i = 0
    while i < len(parts):
        cur = parts[i]
        while i < len(parts) - 1 and len(cur) < min_chars:
            i += 1
            cur += " " + parts[i]
        combined.append(cur)
        i += 1
    return combined


def make_batches(text: str, max_batch_chars: int = 1000) -> List[str]:
    if len(text) < max_batch_chars:
        return [text]
    sentences = split_text_into_sentences(text)
    batches: List[str] = []
    current = ""
    for sentence in sentences:
        if len(current) + len(sentence) > max_batch_chars and current:
            batches.append(current)
            current = sentence
        else:
            if current:
                current += " "
            current += sentence
    if current:
        batches.append(current)
    return batches


def crossfade_pcm(a: bytes, b: bytes, sample_rate: int = 24000, crossfade_ms: int = 50) -> bytes:
    """Crossfade two s16le mono PCM segments."""
    if not a:
        return b
    if not b:
        return a
    n_samples_a = len(a) // 2
    n_samples_b = len(b) // 2
    fade_samples = int(sample_rate * crossfade_ms / 1000)
    fade_samples = min(fade_samples, n_samples_a, n_samples_b)
    if fade_samples <= 0:
        return a + b

    fmt = f"<{n_samples_a}h"
    samples_a = list(struct.unpack(fmt, a))
    fmt_b = f"<{n_samples_b}h"
    samples_b = list(struct.unpack(fmt_b, b))

    out = samples_a[:-fade_samples] if fade_samples < n_samples_a else []
    tail_a = samples_a[-fade_samples:]
    head_b = samples_b[:fade_samples]
    for i in range(fade_samples):
        t = i / max(fade_samples - 1, 1)
        mixed = int(tail_a[i] * (1 - t) + head_b[i] * t)
        out.append(mixed)
    out.extend(samples_b[fade_samples:])
    return struct.pack(f"<{len(out)}h", *out)


def stitch_pcm_segments(segments: Iterable[bytes], sample_rate: int = 24000) -> bytes:
    segments = list(segments)
    if not segments:
        return b""
    result = segments[0]
    for seg in segments[1:]:
        result = crossfade_pcm(result, seg, sample_rate=sample_rate)
    return result


def stream_batches_with_crossfade(
    batch_pcm_lists: List[List[bytes]],
    sample_rate: int = 24000,
) -> Generator[bytes, None, None]:
    """Yield PCM chunks; apply crossfade at batch boundaries on first chunk of each batch."""
    prev_tail: bytes = b""
    for batch_idx, chunks in enumerate(batch_pcm_lists):
        if not chunks:
            continue
        batch_all = b"".join(chunks)
        if batch_idx == 0:
            yield from chunks
            prev_tail = batch_all[-sample_rate * 2 :] if len(batch_all) > sample_rate * 2 else batch_all
            continue
        merged = crossfade_pcm(prev_tail, batch_all, sample_rate=sample_rate)
        overlap = len(prev_tail)
        if overlap > 0 and len(merged) > overlap:
            yield merged[overlap // 2 :]
        else:
            yield from chunks
        prev_tail = batch_all[-sample_rate * 2 :] if len(batch_all) > sample_rate * 2 else batch_all
