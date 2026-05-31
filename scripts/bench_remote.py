#!/usr/bin/env python3
"""Benchmark TTS via public API URL from your laptop (stream / live / async).

Measures inter-chunk gap, RTF, TTFB per request and under concurrent load.

Usage:
  pip install websocket-client

  export API_URL=https://your-lightning-host:8080
  python scripts/bench_remote.py --login

  python scripts/bench_remote.py --mode stream --concurrency 1,4,8
  python scripts/bench_remote.py --mode all --concurrency 4 --requests 12
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from bench_all_modes import (  # noqa: E402
    analyze_async,
    api_json,
    capture_async,
    check_api_reachable,
    login,
)
from debug_pcm_stream import SNAC_CHUNK_SEC, analyze_chunks  # noqa: E402

SAMPLE_RATE = 24000
BYTES_PER_SAMPLE = 2


def percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    s = sorted(values)
    k = (len(s) - 1) * (p / 100.0)
    f = int(k)
    c = min(f + 1, len(s) - 1)
    if f == c:
        return s[f]
    return s[f] + (s[c] - s[f]) * (k - f)


def capture_stream(api_url: str, api_key: str, text: str, voice: str) -> tuple[bytes, list[dict]]:
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
            raise RuntimeError(f"stream HTTP {resp.status}: {resp.read(500)}")
        while True:
            data = resp.read(4096)
            if not data:
                break
            chunks.append(
                {
                    "index": len(chunks),
                    "bytes": len(data),
                    "t_recv": round(time.perf_counter() - t0, 4),
                }
            )
            parts.append(data)
    return b"".join(parts), chunks


def capture_live(api_url: str, api_key: str, text: str, voice: str) -> tuple[bytes, list[dict]]:
    try:
        import websocket
    except ImportError as e:
        raise RuntimeError("pip install websocket-client") from e

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
                # Fallback for servers that only send "done" after client close.
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
                # Utterance finished when server sends utterance_done; idle gap otherwise.
                ws.settimeout(3.0)
    finally:
        ws.close()
    return b"".join(parts), chunks


@dataclass
class RequestResult:
    mode: str
    worker_id: int
    ok: bool
    wall_seconds: float = 0.0
    stats: dict = field(default_factory=dict)
    error: str = ""


CAPTURE_FNS: dict[str, Callable[..., tuple[bytes, Any]]] = {
    "stream": capture_stream,
    "live": capture_live,
}


def run_one(
    mode: str,
    worker_id: int,
    api_url: str,
    api_key: str,
    text: str,
    voice: str,
) -> RequestResult:
    t0 = time.perf_counter()
    try:
        if mode == "async":
            pcm, stats = capture_async(api_url, api_key, text, voice)
            stats["pcm_bytes"] = len(pcm)
        else:
            pcm, chunk_list = CAPTURE_FNS[mode](api_url, api_key, text, voice)
            stats = analyze_chunks(chunk_list)
            stats["pcm_bytes"] = len(pcm)
            if stats.get("inter_chunk_gap_ms_avg") is not None:
                stats["ttfb_ms"] = round(
                    (chunk_list[0]["t_recv"] * 1000) if chunk_list else 0, 1
                )
        wall = time.perf_counter() - t0
        return RequestResult(mode=mode, worker_id=worker_id, ok=True, wall_seconds=wall, stats=stats)
    except Exception as e:
        return RequestResult(
            mode=mode,
            worker_id=worker_id,
            ok=False,
            wall_seconds=time.perf_counter() - t0,
            error=str(e),
        )


def aggregate_results(results: list[RequestResult]) -> dict:
    ok = [r for r in results if r.ok]
    failed = [r for r in results if not r.ok]
    gaps = [
        r.stats["inter_chunk_gap_ms_avg"]
        for r in ok
        if r.stats.get("inter_chunk_gap_ms_avg") is not None
    ]
    rtfs_gap = [
        r.stats["rtf_from_avg_inter_chunk_gap"]
        for r in ok
        if r.stats.get("rtf_from_avg_inter_chunk_gap") is not None
    ]
    rtfs_wall = [
        r.stats["rtf_wall_time_over_audio"]
        for r in ok
        if r.stats.get("rtf_wall_time_over_audio") is not None
    ]
    rtfs_e2e = [
        r.stats["rtf_end_to_end"]
        for r in ok
        if r.stats.get("rtf_end_to_end") is not None
    ]
    ttfb_ms = [r.stats["ttfb_ms"] for r in ok if r.stats.get("ttfb_ms") is not None]
    audio_sec = [r.stats.get("audio_seconds_if_concatenated") or r.stats.get("audio_seconds") for r in ok]
    audio_sec = [a for a in audio_sec if a]

    agg: dict[str, Any] = {
        "total": len(results),
        "ok": len(ok),
        "failed": len(failed),
        "success_rate": round(len(ok) / len(results), 3) if results else 0,
        "errors": [r.error for r in failed[:10]],
    }
    if gaps:
        agg["inter_chunk_gap_ms"] = {
            "avg": round(statistics.mean(gaps), 1),
            "p50": round(percentile(gaps, 50) or 0, 1),
            "p95": round(percentile(gaps, 95) or 0, 1),
            "target_lt_ms": SNAC_CHUNK_SEC * 1000,
        }
    if rtfs_gap:
        agg["rtf_from_avg_inter_chunk_gap"] = {
            "avg": round(statistics.mean(rtfs_gap), 3),
            "p50": round(percentile(rtfs_gap, 50) or 0, 3),
            "p95": round(percentile(rtfs_gap, 95) or 0, 3),
            "realtime_ok_share": round(
                sum(1 for r in ok if r.stats.get("realtime_streaming_ok_rtf_lt_1")) / len(ok), 3
            ),
        }
    if rtfs_wall:
        agg["rtf_wall_time_over_audio"] = {
            "avg": round(statistics.mean(rtfs_wall), 3),
            "p50": round(percentile(rtfs_wall, 50) or 0, 3),
            "p95": round(percentile(rtfs_wall, 95) or 0, 3),
        }
    if rtfs_e2e:
        agg["rtf_end_to_end"] = {
            "avg": round(statistics.mean(rtfs_e2e), 3),
            "p50": round(percentile(rtfs_e2e, 50) or 0, 3),
            "p95": round(percentile(rtfs_e2e, 95) or 0, 3),
        }
    if ttfb_ms:
        agg["ttfb_ms"] = {
            "avg": round(statistics.mean(ttfb_ms), 1),
            "p50": round(percentile(ttfb_ms, 50) or 0, 1),
            "p95": round(percentile(ttfb_ms, 95) or 0, 1),
        }
    if audio_sec and ok:
        total_audio = sum(audio_sec)
        total_wall = sum(r.wall_seconds for r in ok)
        agg["throughput_audio_seconds_per_wall_second"] = (
            round(total_audio / total_wall, 3) if total_wall > 0 else None
        )
    return agg


def run_concurrent(
    mode: str,
    api_url: str,
    api_key: str,
    text: str,
    voice: str,
    concurrency: int,
    requests: int,
) -> dict:
    print(f"\n--- {mode} | concurrency={concurrency} requests={requests} ---")
    t0 = time.perf_counter()
    results: list[RequestResult] = []
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futs = [
            pool.submit(run_one, mode, i, api_url, api_key, text, voice)
            for i in range(requests)
        ]
        for fut in as_completed(futs):
            results.append(fut.result())
    elapsed = time.perf_counter() - t0
    agg = aggregate_results(results)
    agg["concurrency"] = concurrency
    agg["requests"] = requests
    agg["batch_wall_seconds"] = round(elapsed, 3)
    print(json.dumps(agg, indent=2))
    return {"aggregate": agg, "results": [r.__dict__ for r in results]}


def parse_concurrency_list(s: str) -> list[int]:
    return [int(x.strip()) for x in s.split(",") if x.strip()]


def main() -> int:
    p = argparse.ArgumentParser(description="Remote API benchmark with concurrency")
    p.add_argument("--api-url", default=os.environ.get("API_URL", "http://127.0.0.1:8080"))
    p.add_argument("--api-key", default=os.environ.get("API_KEY", ""))
    p.add_argument("--user", default=os.environ.get("DEFAULT_USERNAME", "dev"))
    p.add_argument("--password", default=os.environ.get("DEFAULT_PASSWORD", "devpassword"))
    p.add_argument("--login", action="store_true", help="Print API key from /v1/auth/login and exit")
    p.add_argument(
        "--mode",
        choices=["stream", "live", "async", "all"],
        default="stream",
        help="Endpoint to benchmark",
    )
    p.add_argument(
        "--concurrency",
        default="1,4",
        help="Comma-separated concurrency levels (e.g. 1,4,8)",
    )
    p.add_argument("--requests", type=int, default=8, help="Total requests per concurrency level")
    p.add_argument("--voice", default="tara")
    p.add_argument(
        "--text",
        default=os.environ.get(
            "BENCH_TEXT", "Hello, this is a streaming debug test for Orpheus TTS."
        ),
    )
    p.add_argument("--warmup", type=int, default=1, help="Warm-up requests at concurrency 1")
    p.add_argument("--out", default="bench_remote_out", help="Output directory for manifest.json")
    args = p.parse_args()

    if args.login:
        key = login(args.api_url, args.user, args.password)
        print(key)
        return 0

    check_api_reachable(args.api_url)
    api_key = args.api_key or login(args.api_url, args.user, args.password)
    levels = parse_concurrency_list(args.concurrency)
    modes = ["stream", "live", "async"] if args.mode == "all" else [args.mode]

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    manifest: dict = {
        "api_url": args.api_url,
        "text": args.text,
        "voice": args.voice,
        "modes": {},
    }

    print(f"API: {args.api_url}")
    print(f"SNAC frame ~{SNAC_CHUNK_SEC * 1000:.0f} ms — gap_avg should stay below that for realtime")

    for mode in modes:
        mode_report: dict = {"runs": []}
        if args.warmup > 0 and mode != "async":
            print(f"\n>>> warm-up {mode} x{args.warmup}")
            for i in range(args.warmup):
                r = run_one(mode, i, args.api_url, api_key, args.text, args.voice)
                if not r.ok:
                    print(f"warm-up failed: {r.error}", file=sys.stderr)
                time.sleep(0.5)

        for c in levels:
            run_report = run_concurrent(
                mode, args.api_url, api_key, args.text, args.voice, c, args.requests
            )
            mode_report["runs"].append(run_report)
        manifest["modes"][mode] = mode_report

    path = out / "manifest.json"
    path.write_text(json.dumps(manifest, indent=2))
    print(f"\nWrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
