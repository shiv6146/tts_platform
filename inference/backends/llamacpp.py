"""Token stream via llama.cpp OpenAI-compatible completions API."""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Generator

import requests

from pipeline.prompt import format_prompt

log = logging.getLogger("inference.llamacpp")

REPETITION_PENALTY = 1.1
_server_ready = False


def _api_url() -> str:
    return os.environ.get(
        "LLAMACPP_URL",
        "http://llama-cpp-server:5006/v1/completions",
    )


def _timeout() -> int:
    return int(os.environ.get("ORPHEUS_API_TIMEOUT", "120"))


def _max_tokens() -> int:
    return int(os.environ.get("ORPHEUS_MAX_TOKENS", "8192"))


def _temperature() -> float:
    return float(os.environ.get("ORPHEUS_TEMPERATURE", "0.6"))


def _top_p() -> float:
    return float(os.environ.get("ORPHEUS_TOP_P", "0.9"))


def _model_name() -> str:
    return os.environ.get("ORPHEUS_GGUF_MODEL", "Orpheus-3b-FT-Q8_0.gguf")


def start_wait() -> None:
    import threading

    threading.Thread(target=lambda: wait_for_server(600), name="llama-wait", daemon=True).start()


def wait_for_server(max_wait_sec: int = 600) -> None:
    global _server_ready
    from urllib.parse import urlparse

    parsed = urlparse(_api_url())
    host = parsed.hostname or "llama-cpp-server"
    port = parsed.port or 5006
    base = f"{parsed.scheme}://{host}:{port}"
    probe_urls = [f"{base}/health", f"{base}/v1/models", base]
    deadline = time.time() + max_wait_sec
    while time.time() < deadline:
        for url in probe_urls:
            try:
                r = requests.get(url, timeout=5)
                if r.status_code < 500:
                    log.info("llama.cpp server ready (%s)", url)
                    _server_ready = True
                    return
            except requests.RequestException:
                pass
        try:
            import socket

            with socket.create_connection((host, port), timeout=3):
                log.info("llama.cpp TCP ready %s:%s", host, port)
                _server_ready = True
                return
        except OSError:
            pass
        time.sleep(3)
    raise TimeoutError(f"llama.cpp not ready at {host}:{port} after {max_wait_sec}s")


def server_ready() -> bool:
    return _server_ready


def make_token_generator(
    prompt: str,
    voice: str,
    *,
    request_id: str = "",
) -> Generator[str, None, None]:
    del request_id
    formatted = format_prompt(prompt, voice)
    payload = {
        "prompt": formatted,
        "max_tokens": _max_tokens(),
        "temperature": _temperature(),
        "top_p": _top_p(),
        "repeat_penalty": REPETITION_PENALTY,
        "stream": True,
        "model": _model_name(),
    }
    session = requests.Session()
    response = session.post(
        _api_url(),
        headers={"Content-Type": "application/json"},
        json=payload,
        stream=True,
        timeout=_timeout(),
    )
    if response.status_code != 200:
        raise RuntimeError(f"llama.cpp API {response.status_code}: {response.text[:500]}")

    for line in response.iter_lines():
        if not line:
            continue
        line_str = line.decode("utf-8")
        if not line_str.startswith("data: "):
            continue
        data_str = line_str[6:]
        if data_str.strip() == "[DONE]":
            break
        try:
            data = json.loads(data_str)
            if "choices" in data and data["choices"]:
                token_chunk = data["choices"][0].get("text", "")
                for token_text in token_chunk.split(">"):
                    token_text = f"{token_text}>"
                    if token_text and token_text != ">":
                        yield token_text
        except json.JSONDecodeError:
            continue
