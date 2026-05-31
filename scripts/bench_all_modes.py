#!/usr/bin/env python3
"""Benchmark async, HTTP stream, and live WS through the API (end-to-end).

Also reports direct gRPC stream timing when --grpc is set (SNAC chunk boundaries).

Usage:
  API_URL=http://api:8080 python scripts/bench_all_modes.py
  API_URL=http://127.0.0.1:8080 API_KEY=sk-... python scripts/bench_all_modes.py --grpc
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
import wave
from io import BytesIO
from pathlib import Path

# Reuse chunk analysis from debug_pcm_stream when co-located in scripts/
_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
from debug_pcm_stream import (  # noqa: E402
    analyze_chunks,
    capture_grpc_synthesize,
    capture_http_stream,
)

SAMPLE_RATE = 24000
BYTES_PER_SAMPLE = 2


def api_json(
    method: str,
    url: str,
    api_key: str,
    body: dict | None = None,
    timeout: int = 600,
) -> tuple[int, dict | bytes]:
    data = None if body is None else json.dumps(body).encode()
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            if "application/json" in resp.headers.get("Content-Type", ""):
                return resp.status, json.loads(raw.decode())
            return resp.status, raw
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw.decode())
        except Exception:
            return e.code, raw


def login(api_url: str, user: str, password: str) -> str:
    status, data = api_json(
        "POST",
        f"{api_url.rstrip('/')}/v1/auth/login",
        "",
        {"username": user, "password": password},
    )
    if status != 200 or not isinstance(data, dict):
        raise RuntimeError(f"login failed HTTP {status}: {data}")
    return data["apiKey"]


def pcm_seconds(pcm_len: int) -> float:
    return pcm_len / (SAMPLE_RATE * BYTES_PER_SAMPLE)


def wav_to_pcm(wav_bytes: bytes) -> bytes:
    with wave.open(BytesIO(wav_bytes), "rb") as wf:
        return wf.readframes(wf.getnframes())


def analyze_async(wall_sec: float, audio_sec: float) -> dict:
    rtf = round(wall_sec / audio_sec, 3) if audio_sec > 0 and wall_sec > 0 else None
    return {
        "audio_seconds": round(audio_sec, 3),
        "wall_seconds_end_to_end": round(wall_sec, 3),
        "rtf_end_to_end": rtf,
        "realtime_ok_rtf_lt_1": rtf is not None and rtf < 1.0,
        "note": "async returns full clip; no inter-chunk gaps",
    }


def capture_async(api_url: str, api_key: str, text: str, voice: str) -> tuple[bytes, dict]:
    t0 = time.perf_counter()
    status, data = api_json(
        "POST",
        f"{api_url.rstrip('/')}/v1/tts/async",
        api_key,
        {"text": text, "voice": voice},
    )
    if status not in (200, 202) or not isinstance(data, dict):
        raise RuntimeError(f"async create HTTP {status}: {data}")
    job_id = data.get("jobId")
    if not job_id:
        raise RuntimeError(f"async missing jobId: {data}")

    deadline = time.time() + 600
    st = "pending"
    while time.time() < deadline:
        time.sleep(1.0)
        _, st_data = api_json(
            "GET",
            f"{api_url.rstrip('/')}/v1/tts/async/{job_id}",
            api_key,
        )
        if isinstance(st_data, dict):
            st = st_data.get("status", "")
            if st in ("completed", "failed"):
                break
    if st != "completed":
        raise RuntimeError(f"async job {job_id} status={st}")

    audio_status, audio_body = api_json(
        "GET",
        f"{api_url.rstrip('/')}/v1/tts/async/{job_id}/audio",
        api_key,
    )
    if audio_status != 200 or not isinstance(audio_body, bytes):
        raise RuntimeError(f"async audio HTTP {audio_status}")
    pcm = wav_to_pcm(audio_body)
    wall = time.perf_counter() - t0
    return pcm, analyze_async(wall, pcm_seconds(len(pcm)))


def capture_live(api_url: str, api_key: str, text: str, voice: str) -> tuple[bytes, list[dict]]:
    try:
        import websocket
    except ImportError as e:
        raise RuntimeError("pip install websocket-client for live bench") from e

    ws_url = api_url.rstrip("/").replace("http://", "ws://").replace("https://", "wss://")
    ws_url += "/v1/tts/live"
    parts: list[bytes] = []
    chunks: list[dict] = []
    phrase_start: float | None = None
    ws = websocket.create_connection(
        ws_url, header=[f"Authorization: Bearer {api_key}"], timeout=600
    )
    try:
        import websocket as _ws_mod

        ws.settimeout(600)
        sent = False
        while True:
            try:
                msg = ws.recv()
            except _ws_mod.WebSocketTimeoutException:
                if sent and chunks:
                    break
                raise
            if isinstance(msg, str):
                j = json.loads(msg)
                if j.get("type") == "ready" and not sent:
                    sent = True
                    phrase_start = time.perf_counter()
                    ws.send(
                        json.dumps(
                            {
                                "type": "text",
                                "text": text,
                                "final": True,
                                "voice": voice,
                            }
                        )
                    )
                if j.get("type") in (
                    "utterance_done",
                    "done",
                    "error",
                    "insufficient_balance",
                ):
                    if j.get("type") == "error":
                        raise RuntimeError(j.get("error", "live error"))
                    break
            else:
                data = bytes(msg)
                if not data or phrase_start is None:
                    continue
                chunks.append(
                    {
                        "index": len(chunks),
                        "bytes": len(data),
                        "t_recv": round(time.perf_counter() - phrase_start, 4),
                    }
                )
                parts.append(data)
                ws.settimeout(3.0)
    finally:
        ws.close()
    return b"".join(parts), chunks


def run_mode(
    name: str,
    fn,
    manifest: dict,
    out: Path,
    *args,
) -> dict:
    print(f"\n==> {name}")
    pcm, extra = fn(*args)
    if isinstance(extra, list):
        stats = analyze_chunks(extra)
        stats["pcm_bytes"] = len(pcm)
    else:
        stats = extra
        stats["pcm_bytes"] = len(pcm)
    print(json.dumps(stats, indent=2))
    manifest["tests"][name] = stats
    (out / f"{name}.raw").write_bytes(pcm)
    return stats


def check_api_reachable(api_url: str, timeout: int = 10) -> None:
    url = f"{api_url.rstrip('/')}/health"
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status != 200:
                raise RuntimeError(f"GET {url} -> HTTP {resp.status}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"cannot reach API at {api_url}: {e}") from e


def main() -> int:
    p = argparse.ArgumentParser(description="Benchmark async / stream / live (+ optional gRPC)")
    p.add_argument("--api-url", default=os.environ.get("API_URL", "http://127.0.0.1:8080"))
    p.add_argument("--api-key", default=os.environ.get("API_KEY", ""))
    p.add_argument("--user", default=os.environ.get("DEFAULT_USERNAME", "dev"))
    p.add_argument("--password", default=os.environ.get("DEFAULT_PASSWORD", "devpassword"))
    p.add_argument("--grpc-addr", default=os.environ.get("GRPC_ADDR", "127.0.0.1:50051"))
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--grpc-only", action="store_true", help="Direct gRPC Synthesize only")
    mode.add_argument("--api-only", action="store_true", help="HTTP async / stream / live only")
    p.add_argument("--grpc", action="store_true", help="Include gRPC when running all API modes")
    p.add_argument("--text", default=os.environ.get(
        "BENCH_TEXT", "Hello, this is a streaming debug test for Orpheus TTS."
    ))
    p.add_argument("--voice", default="tara")
    p.add_argument("--out-dir", default="/tmp/bench_all_modes")
    args = p.parse_args()

    run_api = args.api_only or not args.grpc_only
    run_grpc = args.grpc_only or args.grpc

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    manifest: dict = {
        "api_url": args.api_url if run_api else None,
        "grpc_addr": args.grpc_addr if run_grpc else None,
        "text": args.text,
        "voice": args.voice,
        "tests": {},
    }

    if run_grpc:
        pcm, gchunks = capture_grpc_synthesize(args.grpc_addr, args.text, args.voice)
        gstats = analyze_chunks(gchunks)
        gstats["pcm_bytes"] = len(pcm)
        print("\n==> stream_grpc (direct inference)")
        print(json.dumps(gstats, indent=2))
        manifest["tests"]["stream_grpc"] = gstats
        (out / "stream_grpc.raw").write_bytes(pcm)

    if not run_api:
        _write_manifest(out, manifest)
        return 0

    check_api_reachable(args.api_url)
    api_key = args.api_key or login(args.api_url, args.user, args.password)

    pcm, chunks = capture_http_stream(args.api_url, api_key, args.text, args.voice)
    stats = analyze_chunks(chunks)
    stats["pcm_bytes"] = len(pcm)
    print("\n==> stream (HTTP /v1/tts/stream)")
    print(json.dumps(stats, indent=2))
    manifest["tests"]["stream"] = stats
    (out / "stream.raw").write_bytes(pcm)

    pcm, astats = capture_async(args.api_url, api_key, args.text, args.voice)
    print("\n==> async (HTTP /v1/tts/async)")
    astats["pcm_bytes"] = len(pcm)
    print(json.dumps(astats, indent=2))
    manifest["tests"]["async"] = astats
    (out / "async.raw").write_bytes(pcm)

    pcm, lchunks = capture_live(args.api_url, api_key, args.text, args.voice)
    lstats = analyze_chunks(lchunks)
    lstats["pcm_bytes"] = len(pcm)
    print("\n==> live (WS /v1/tts/live)")
    print(json.dumps(lstats, indent=2))
    manifest["tests"]["live"] = lstats
    (out / "live.raw").write_bytes(pcm)

    if run_grpc and not args.grpc_only:
        pcm, gchunks = capture_grpc_synthesize(args.grpc_addr, args.text, args.voice)
        gstats = analyze_chunks(gchunks)
        gstats["pcm_bytes"] = len(pcm)
        print("\n==> stream_grpc (direct inference)")
        print(json.dumps(gstats, indent=2))
        manifest["tests"]["stream_grpc"] = gstats
        (out / "stream_grpc.raw").write_bytes(pcm)

    _write_manifest(out, manifest)
    return 0


def _write_manifest(out: Path, manifest: dict) -> None:
    manifest_path = out / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2))
    print(f"\nWrote {manifest_path}")
    print("\n=== summary ===")
    for mode, st in manifest["tests"].items():
        rtf = st.get("rtf_from_avg_inter_chunk_gap") or st.get("rtf_end_to_end")
        ok = st.get("realtime_streaming_ok_rtf_lt_1", st.get("realtime_ok_rtf_lt_1"))
        gap = st.get("inter_chunk_gap_ms_avg")
        gap_s = f" gap_avg={gap}ms" if gap is not None else ""
        print(f"  {mode:14} rtf={rtf} realtime_ok={ok}{gap_s}")


if __name__ == "__main__":
    raise SystemExit(main())
