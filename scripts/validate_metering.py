#!/usr/bin/env python3
"""Validate wallet debits vs PCM duration for stream, async, and live modes.

Usage:
  API_URL=http://127.0.0.1:8080 USER=dev PASS=devpassword python scripts/validate_metering.py
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
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

SAMPLE_RATE = 24000
BYTES_PER_SAMPLE = 2


@dataclass
class WalletSnap:
    balance_usd: float
    price_per_audio_minute_usd: float


@dataclass
class ModeResult:
    mode: str
    pcm: bytes
    audio_seconds: float
    expected_cost: float
    http_status: int = 200


def api_json(
    method: str,
    url: str,
    api_key: str,
    body: dict | None = None,
    timeout: int = 600,
) -> tuple[int, dict | bytes]:
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
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


def get_wallet(api_url: str, api_key: str) -> WalletSnap:
    status, data = api_json("GET", f"{api_url.rstrip('/')}/v1/wallet", api_key)
    if status != 200 or not isinstance(data, dict):
        raise RuntimeError(f"wallet HTTP {status}: {data}")
    return WalletSnap(
        float(data["balanceUsd"]),
        float(data["pricePerAudioMinuteUsd"]),
    )


def pcm_seconds(pcm_len: int) -> float:
    return pcm_len / (SAMPLE_RATE * BYTES_PER_SAMPLE)


def expected_cost(audio_seconds: float, ppm: float) -> float:
    if audio_seconds <= 0:
        return 0.0
    return (audio_seconds / 60.0) * ppm


def capture_stream(api_url: str, api_key: str, text: str, voice: str) -> ModeResult:
    status, body = api_json(
        "POST",
        f"{api_url.rstrip('/')}/v1/tts/stream",
        api_key,
        {"text": text, "voice": voice},
    )
    if status != 200 or not isinstance(body, bytes):
        return ModeResult("http_stream", b"", 0.0, 0.0, status)
    sec = pcm_seconds(len(body))
    return ModeResult("http_stream", body, sec, 0.0, status)


def wav_to_pcm(wav_bytes: bytes) -> bytes:
    with wave.open(BytesIO(wav_bytes), "rb") as wf:
        return wf.readframes(wf.getnframes())


def capture_async(api_url: str, api_key: str, text: str, voice: str) -> ModeResult:
    status, data = api_json(
        "POST",
        f"{api_url.rstrip('/')}/v1/tts/async",
        api_key,
        {"text": text, "voice": voice},
    )
    if status not in (200, 202) or not isinstance(data, dict):
        return ModeResult("http_async", b"", 0.0, 0.0, status)
    job_id = data.get("jobId")
    if not job_id:
        return ModeResult("http_async", b"", 0.0, 0.0, status)

    deadline = time.time() + 300
    st = "pending"
    while time.time() < deadline:
        time.sleep(1.5)
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
        return ModeResult("http_async", b"", 0.0, 0.0, 500)

    audio_status, audio_body = api_json(
        "GET",
        f"{api_url.rstrip('/')}/v1/tts/async/{job_id}/audio",
        api_key,
    )
    if audio_status != 200 or not isinstance(audio_body, bytes):
        return ModeResult("http_async", b"", 0.0, 0.0, audio_status)
    pcm = wav_to_pcm(audio_body)
    return ModeResult("http_async", pcm, pcm_seconds(len(pcm)), 0.0, audio_status)


def capture_live(api_url: str, api_key: str, text: str, voice: str) -> ModeResult:
    try:
        import websocket
    except ImportError:
        print("pip install websocket-client for live mode", file=sys.stderr)
        return ModeResult("websocket", b"", 0.0, 0.0, 0)

    ws_url = api_url.rstrip("/").replace("http://", "ws://").replace("https://", "wss://")
    ws_url += "/v1/tts/live"
    parts: list[bytes] = []
    ws = websocket.create_connection(
        ws_url, header=[f"Authorization: Bearer {api_key}"], timeout=300
    )
    try:
        ws.settimeout(300)
        sent = False
        t0 = time.time()
        while time.time() - t0 < 300:
            msg = ws.recv()
            if isinstance(msg, str):
                j = json.loads(msg)
                if j.get("type") == "ready" and not sent:
                    sent = True
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
                if j.get("type") in ("done", "error", "insufficient_balance"):
                    break
            else:
                parts.append(bytes(msg))
    finally:
        ws.close()

    pcm = b"".join(parts)
    return ModeResult("websocket", pcm, pcm_seconds(len(pcm)), 0.0, 200)


def write_wav(path: Path, pcm: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(pcm)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--api-url", default=os.environ.get("API_URL", "http://127.0.0.1:8080"))
    p.add_argument("--api-key", default=os.environ.get("API_KEY", ""))
    p.add_argument("--user", default=os.environ.get("DEFAULT_USERNAME", "dev"))
    p.add_argument("--password", default=os.environ.get("DEFAULT_PASSWORD", "devpassword"))
    p.add_argument("--voice", default="tara")
    p.add_argument("--settle-sec", type=float, default=2.5)
    p.add_argument("--tolerance-ratio", type=float, default=0.2)
    p.add_argument("--tolerance-usd", type=float, default=0.03)
    p.add_argument("--skip-live", action="store_true")
    p.add_argument("--out-dir", default="")
    args = p.parse_args()

    api_key = args.api_key or login(args.api_url, args.user, args.password)
    before = get_wallet(args.api_url, api_key)
    ppm = before.price_per_audio_minute_usd
    print(f"Wallet before: ${before.balance_usd:.4f}  rate=${ppm:.4f}/min")

    tests = [
        ("stream", capture_stream, "Metering stream validation."),
        ("async", capture_async, "Metering async validation."),
    ]
    if not args.skip_live:
        tests.append(("live", capture_live, "Metering live validation."))

    results: list[ModeResult] = []
    for name, fn, text in tests:
        print(f"\n==> {name}")
        r = fn(args.api_url, api_key, text, args.voice)
        r.expected_cost = expected_cost(r.audio_seconds, ppm)
        results.append(r)
        print(
            f"  transport={r.mode} pcm={len(r.pcm)}B audio={r.audio_seconds:.3f}s "
            f"expected=${r.expected_cost:.4f} status={r.http_status}"
        )
        if len(r.pcm) < 1000:
            print(f"  WARN: PCM too small for {name}", file=sys.stderr)
        if args.out_dir:
            write_wav(Path(args.out_dir) / f"{name}.wav", r.pcm)

    print(f"\nWaiting {args.settle_sec}s for metering…")
    time.sleep(args.settle_sec)
    after = get_wallet(args.api_url, api_key)
    total_expected = sum(r.expected_cost for r in results)
    total_delta = before.balance_usd - after.balance_usd
    print(f"Wallet after:  ${after.balance_usd:.4f}")
    print(f"Expected debit: ${total_expected:.4f}  actual: ${total_delta:.4f}")

    tol = max(args.tolerance_usd, total_expected * args.tolerance_ratio)
    if abs(total_delta - total_expected) > tol:
        print(
            f"FAIL: |delta - expected| = {abs(total_delta - total_expected):.4f} > {tol:.4f}",
            file=sys.stderr,
        )
        print(
            "Note: billing coalesces PCM windows; small drift vs raw PCM length is normal.",
            file=sys.stderr,
        )
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
