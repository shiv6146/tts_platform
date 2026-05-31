#!/usr/bin/env python3
"""Capture PCM from API stream, gRPC Synthesize, and analyze chunk timing.

Writes a manifest JSON plus concatenated raw PCM/WAV under --out-dir.
Play the WAV with: ffplay -f s16le -ar 24000 -ac 1 out/stream_api.wav

Usage:
  API_KEY=sk-... python scripts/debug_pcm_stream.py
  API_KEY=sk-... GRPC_ADDR=inference:50051 python scripts/debug_pcm_stream.py --grpc
"""

from __future__ import annotations

import argparse
import json
import os
import struct
import sys
import time
import wave
from pathlib import Path

SAMPLE_RATE = 24000
BYTES_PER_SAMPLE = 2


def write_wav(path: Path, pcm: bytes, sample_rate: int = SAMPLE_RATE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm)


def boundary_rms_jump(pcm: bytes, chunk_size: int = 4096) -> float | None:
    """RMS level jump at fixed chunk boundaries (high => overlap/stutter risk)."""
    if len(pcm) < chunk_size * 2:
        return None
    import struct

    def rms_at(off: int) -> float:
        n = min(256, (len(pcm) - off) // 2)
        if n <= 0:
            return 0.0
        samples = struct.unpack(f"<{n}h", pcm[off : off + n * 2])
        return (sum(s * s for s in samples) / n) ** 0.5

    jumps = []
    for i in range(chunk_size, len(pcm) - 256, chunk_size):
        jumps.append(abs(rms_at(i) - rms_at(i - 2)))
    return max(jumps) if jumps else None


def analyze_chunks(chunks: list[dict]) -> dict:
    if not chunks:
        return {"error": "no chunks"}
    sizes = [c["bytes"] for c in chunks]
    gaps_ms = []
    for i in range(1, len(chunks)):
        gaps_ms.append((chunks[i]["t_recv"] - chunks[i - 1]["t_recv"]) * 1000)
    total_bytes = sum(sizes)
    audio_sec = total_bytes / (SAMPLE_RATE * BYTES_PER_SAMPLE)
    return {
        "chunk_count": len(chunks),
        "total_bytes": total_bytes,
        "audio_seconds_if_concatenated": round(audio_sec, 3),
        "chunk_bytes_min": min(sizes),
        "chunk_bytes_max": max(sizes),
        "chunk_bytes_avg": round(sum(sizes) / len(sizes), 1),
        "inter_chunk_gap_ms_min": round(min(gaps_ms), 1) if gaps_ms else None,
        "inter_chunk_gap_ms_max": round(max(gaps_ms), 1) if gaps_ms else None,
        "inter_chunk_gap_ms_avg": round(sum(gaps_ms) / len(gaps_ms), 1) if gaps_ms else None,
        "burst_groups_gap_lt_5ms": sum(1 for g in gaps_ms if g < 5),
        "pause_gaps_gt_100ms": sum(1 for g in gaps_ms if g > 100),
    }


def capture_http_stream(api_url: str, api_key: str, text: str, voice: str) -> tuple[bytes, list[dict]]:
    """Note: HTTP body reads are TCP-sized, not SNAC frame boundaries. Use --grpc for per-chunk timing."""
    import urllib.request

    body = json.dumps({"text": text, "voice": voice}).encode()
    req = urllib.request.Request(
        f"{api_url.rstrip('/')}/v1/tts/stream",
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    chunks: list[dict] = []
    parts: list[bytes] = []
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=600) as resp:
        if resp.status != 200:
            raise RuntimeError(f"HTTP {resp.status}: {resp.read(500)}")
        while True:
            data = resp.read(4096)
            if not data:
                break
            t_recv = time.perf_counter() - t0
            chunks.append({"index": len(chunks), "bytes": len(data), "t_recv": round(t_recv, 4)})
            parts.append(data)
    return b"".join(parts), chunks


def capture_grpc_synthesize(grpc_addr: str, text: str, voice: str) -> tuple[bytes, list[dict]]:
    import grpc

    root = Path(__file__).resolve().parents[1] / "inference"
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from tts.v1 import inference_pb2, inference_pb2_grpc

    chunks: list[dict] = []
    parts: list[bytes] = []
    t0 = time.perf_counter()
    with grpc.insecure_channel(grpc_addr) as ch:
        stub = inference_pb2_grpc.TTSInferenceStub(ch)
        stream = stub.Synthesize(
            inference_pb2.SynthesizeRequest(
                request_id="debug-pcm",
                text=text,
                voice=voice,
            )
        )
        for msg in stream:
            pcm = bytes(msg.pcm)
            if not pcm:
                continue
            t_recv = time.perf_counter() - t0
            chunks.append(
                {
                    "index": len(chunks),
                    "bytes": len(pcm),
                    "t_recv": round(t_recv, 4),
                    "seq": msg.seq,
                    "sample_rate": msg.sample_rate,
                }
            )
            parts.append(pcm)
    return b"".join(parts), chunks


def main() -> int:
    p = argparse.ArgumentParser(description="Debug TTS PCM streaming chunk timing")
    p.add_argument("--api-url", default=os.environ.get("API_URL", "http://127.0.0.1:8080"))
    p.add_argument("--api-key", default=os.environ.get("API_KEY", ""))
    p.add_argument("--grpc-addr", default=os.environ.get("GRPC_ADDR", "127.0.0.1:50051"))
    p.add_argument("--grpc", action="store_true", help="Also test direct gRPC Synthesize")
    p.add_argument("--text", default="Hello, this is a streaming debug test for Orpheus TTS.")
    p.add_argument("--voice", default="tara")
    p.add_argument("--out-dir", default="debug_pcm_out")
    args = p.parse_args()

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    manifest: dict = {"text": args.text, "voice": args.voice, "tests": {}}

    if not args.grpc:
        if not args.api_key:
            print("Set API_KEY for HTTP stream test", file=sys.stderr)
            return 1
        print("==> HTTP POST /v1/tts/stream")
        pcm, chunks = capture_http_stream(args.api_url, args.api_key, args.text, args.voice)
        tag = "stream_api"
        write_wav(out / f"{tag}.wav", pcm)
        (out / f"{tag}.raw").write_bytes(pcm)
        stats = analyze_chunks(chunks)
        jump = boundary_rms_jump(pcm, 4096)
        if jump is not None:
            stats["boundary_rms_jump_max_4096"] = round(jump, 1)
        manifest["tests"][tag] = {"stats": stats, "chunks": chunks[:200]}
        print(json.dumps(stats, indent=2))

    if args.grpc:
        print("==> gRPC Synthesize (inference direct)")
        pcm, chunks = capture_grpc_synthesize(args.grpc_addr, args.text, args.voice)
        tag = "stream_grpc"
        write_wav(out / f"{tag}.wav", pcm)
        (out / f"{tag}.raw").write_bytes(pcm)
        stats = analyze_chunks(chunks)
        jump = boundary_rms_jump(pcm, 4096)
        if jump is not None:
            stats["boundary_rms_jump_max_4096"] = round(jump, 1)
        manifest["tests"][tag] = {"stats": stats, "chunks": chunks[:200]}
        print(json.dumps(stats, indent=2))

    manifest_path = out / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2))
    print(f"Wrote {manifest_path}")
    print(f"Play: ffplay -f s16le -ar {SAMPLE_RATE} -ac 1 {out}/stream_*.raw")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
