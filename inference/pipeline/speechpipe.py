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
_cuda_stream = None


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
    global _snac_model, _snac_device, _cuda_stream
    if _snac_model is not None:
        return
    _snac_device = _resolve_snac_device()
    log.info("loading SNAC on device=%s", _snac_device)
    _snac_model = SNAC.from_pretrained("hubertsiuzdak/snac_24khz").eval().to(_snac_device)
    if _snac_device == "cuda":
        _cuda_stream = torch.cuda.Stream()


def snac_ready() -> bool:
    return _snac_model is not None


def convert_to_audio(multiframe, count):
    ensure_snac_loaded()
    if len(multiframe) < 7:
        return None

    num_frames = len(multiframe) // 7
    frame = multiframe[: num_frames * 7]
    device = _snac_device

    codes_0 = torch.zeros(num_frames, dtype=torch.int32, device=device)
    codes_1 = torch.zeros(num_frames * 2, dtype=torch.int32, device=device)
    codes_2 = torch.zeros(num_frames * 4, dtype=torch.int32, device=device)
    frame_tensor = torch.tensor(frame, dtype=torch.int32, device=device)

    for j in range(num_frames):
        idx = j * 7
        codes_0[j] = frame_tensor[idx]
        codes_1[j * 2] = frame_tensor[idx + 1]
        codes_1[j * 2 + 1] = frame_tensor[idx + 4]
        codes_2[j * 4] = frame_tensor[idx + 2]
        codes_2[j * 4 + 1] = frame_tensor[idx + 3]
        codes_2[j * 4 + 2] = frame_tensor[idx + 5]
        codes_2[j * 4 + 3] = frame_tensor[idx + 6]

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

    stream_ctx = torch.cuda.stream(_cuda_stream) if _cuda_stream is not None else torch.no_grad()
    with stream_ctx, torch.inference_mode():
        audio_hat = _snac_model.decode(codes)
        audio_slice = audio_hat[:, :, 2048:4096]
        if device == "cuda":
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
    buffer = []
    count = 0
    first_chunk_processed = False
    min_frames_first = 7
    min_frames_subsequent = 28
    ideal_frames = 49
    process_every_n = 7

    async for token_sim in token_gen:
        token = turn_token_into_id(token_sim, count)
        if token is not None and token > 0:
            buffer.append(token)
            count += 1
            if not first_chunk_processed:
                if count >= min_frames_first:
                    buffer_to_proc = buffer[-min_frames_first:]
                    audio_samples = convert_to_audio(buffer_to_proc, count)
                    if audio_samples is not None:
                        first_chunk_processed = True
                        yield audio_samples
            else:
                if count % process_every_n == 0:
                    if len(buffer) >= ideal_frames:
                        buffer_to_proc = buffer[-ideal_frames:]
                    elif len(buffer) >= min_frames_subsequent:
                        buffer_to_proc = buffer[-min_frames_subsequent:]
                    else:
                        continue
                    audio_samples = convert_to_audio(buffer_to_proc, count)
                    if audio_samples is not None:
                        yield audio_samples

    if len(buffer) >= ideal_frames:
        audio_samples = convert_to_audio(buffer[-ideal_frames:], count)
        if audio_samples is not None:
            yield audio_samples
    elif len(buffer) >= min_frames_subsequent:
        audio_samples = convert_to_audio(buffer[-min_frames_subsequent:], count)
        if audio_samples is not None:
            yield audio_samples
    elif len(buffer) >= process_every_n:
        last_token = buffer[-1]
        padding = [last_token] * (min_frames_subsequent - len(buffer))
        audio_samples = convert_to_audio(buffer + padding, count)
        if audio_samples is not None:
            yield audio_samples


def tokens_decoder_sync(syn_token_gen):
    max_queue_size = 32 if _snac_device == "cuda" else 8
    audio_queue: queue.Queue = queue.Queue(maxsize=max_queue_size)
    batch_size = 16 if _snac_device == "cuda" else 4

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

    # Yield each SNAC frame as soon as it is ready. Batching (e.g. 5 chunks) caused
    # ~400ms bursts then silence — audible stutter in stream/live playback.
    while True:
        audio = audio_queue.get()
        if audio is None:
            break
        yield audio
    thread.join(timeout=30)
