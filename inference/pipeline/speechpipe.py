# Adapted from Orpheus-FastAPI tts_engine/speechpipe.py (Apache-2.0)
from __future__ import annotations

import asyncio
import logging
import os
import queue
import threading
import time

import torch
from snac import SNAC

log = logging.getLogger("inference.speechpipe")

CUSTOM_TOKEN_PREFIX = "<custom_token_"
token_id_cache: dict = {}
MAX_CACHE_SIZE = 10000

_snac_model = None
_snac_device: str | None = None
_decode_stream_local = threading.local()


def _resolve_snac_device() -> str:
    explicit = os.environ.get("SNAC_DEVICE", "").strip().lower()
    if explicit:
        return explicit
    if torch.cuda.is_available():
        return "cuda"
    if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def ensure_snac_loaded() -> None:
    global _snac_model, _snac_device
    if _snac_model is not None:
        return
    _snac_device = _resolve_snac_device()
    log.info("loading SNAC on device=%s", _snac_device)
    _snac_model = SNAC.from_pretrained("hubertsiuzdak/snac_24khz").eval().to(_snac_device)


def snac_ready() -> bool:
    return _snac_model is not None


def _decode_stream() -> torch.cuda.Stream | None:
    """Per-thread CUDA stream so concurrent gRPC workers decode in parallel."""
    if _snac_device != "cuda":
        return None
    stream = getattr(_decode_stream_local, "stream", None)
    if stream is None:
        stream = torch.cuda.Stream()
        _decode_stream_local.stream = stream
    return stream


def convert_to_audio(multiframe, count):
    ensure_snac_loaded()
    if len(multiframe) < 7:
        return None

    num_frames = len(multiframe) // 7
    frame = multiframe[: num_frames * 7]
    device = _snac_device

    frame_tensor = torch.tensor(frame, dtype=torch.int32, device=device).view(num_frames, 7)
    codes_0 = frame_tensor[:, 0]
    codes_1 = torch.stack((frame_tensor[:, 1], frame_tensor[:, 4]), dim=1).reshape(num_frames * 2)
    codes_2 = torch.stack(
        (frame_tensor[:, 2], frame_tensor[:, 3], frame_tensor[:, 5], frame_tensor[:, 6]),
        dim=1,
    ).reshape(num_frames * 4)

    codes = [codes_0.unsqueeze(0), codes_1.unsqueeze(0), codes_2.unsqueeze(0)]
    if (
        torch.any(codes[0] < 0)
        or torch.any(codes[0] > 4096)
        or torch.any(codes[1] < 0)
        or torch.any(codes[1] > 4096)
        or torch.any(codes[2] < 0)
        or torch.any(codes[2] > 4096)
    ):
        return None

    decode_stream = _decode_stream()
    if decode_stream is not None:
        stream_ctx = torch.cuda.stream(decode_stream)
    else:
        stream_ctx = torch.no_grad()

    with stream_ctx, torch.inference_mode():
        audio_hat = _snac_model.decode(codes)
        audio_slice = audio_hat[:, :, 2048:4096]
        if device == "cuda":
            if decode_stream is not None:
                decode_stream.synchronize()
            audio_bytes = (audio_slice * 32767).to(torch.int16).cpu().numpy().tobytes()
        else:
            audio_np = audio_slice.detach().cpu().numpy()
            audio_bytes = (audio_np * 32767).astype("int16").tobytes()
    return audio_bytes


def turn_token_into_id(token_string, index):
    cache_key = (token_string, index % 7)
    if cache_key in token_id_cache:
        return token_id_cache[cache_key]
    if CUSTOM_TOKEN_PREFIX not in token_string:
        return None
    token_string = token_string.strip()
    last_token_start = token_string.rfind(CUSTOM_TOKEN_PREFIX)
    if last_token_start == -1:
        return None
    last_token = token_string[last_token_start:]
    if not (last_token.startswith(CUSTOM_TOKEN_PREFIX) and last_token.endswith(">")):
        return None
    try:
        number_str = last_token[14:-1]
        token_id = int(number_str) - 10 - ((index % 7) * 4096)
        if len(token_id_cache) < MAX_CACHE_SIZE:
            token_id_cache[cache_key] = token_id
        return token_id
    except (ValueError, IndexError):
        return None


async def tokens_decoder(token_gen):
    """Canonical Orpheus-TTS SNAC streaming (orpheus_tts/decoder.py).

    Yields ~4096 bytes (~85ms @ 24kHz) per step from audio_hat[:, :, 2048:4096].
    """
    buffer = []
    count = 0

    async for token_sim in token_gen:
        token = turn_token_into_id(token_sim, count)
        if token is not None and token > 0:
            buffer.append(token)
            count += 1
            if count % 7 == 0 and count > 27:
                buffer_to_proc = buffer[-28:]
                audio_samples = convert_to_audio(buffer_to_proc, count)
                if audio_samples is not None:
                    yield audio_samples

    if count > 27 and count % 7 != 0:
        buffer_to_proc = buffer[-28:]
        audio_samples = convert_to_audio(buffer_to_proc, count)
        if audio_samples is not None:
            yield audio_samples


def tokens_decoder_sync(syn_token_gen):
    import hardware

    max_queue_size = hardware.DECODER_QUEUE_SIZE if _snac_device == "cuda" else 8
    audio_queue: queue.Queue = queue.Queue(maxsize=max_queue_size)
    batch_size = hardware.TOKEN_BATCH_SIZE if _snac_device == "cuda" else 4

    async def async_token_gen():
        token_batch = []
        for token in syn_token_gen:
            token_batch.append(token)
            if len(token_batch) >= batch_size:
                for t in token_batch:
                    yield t
                token_batch = []
        for t in token_batch:
            yield t

    async def async_producer():
        try:
            async for audio_chunk in tokens_decoder(async_token_gen()):
                if audio_chunk:
                    audio_queue.put(audio_chunk)
        except Exception:
            log.exception("audio producer error")
        finally:
            audio_queue.put(None)

    def run_async():
        asyncio.run(async_producer())

    thread = threading.Thread(target=run_async, daemon=True)
    thread.start()

    while True:
        audio = audio_queue.get()
        if audio is None:
            break
        yield audio
    thread.join(timeout=30)
