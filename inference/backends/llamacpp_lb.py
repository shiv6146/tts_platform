"""Load-balancing helpers for multiple llama.cpp HTTP backends."""

from __future__ import annotations

import os
import threading
from urllib.parse import urlparse

_rr = 0
_lock = threading.Lock()
_urls: list[str] | None = None


def _split_urls(raw: str) -> list[str]:
    return [u.strip() for u in raw.split(",") if u.strip()]


def completion_urls() -> list[str]:
    """Full /v1/completions URLs (one or many)."""
    global _urls
    if _urls is not None:
        return _urls
    multi = os.environ.get("LLAMACPP_URLS", "").strip()
    if multi:
        _urls = _split_urls(multi)
        return _urls
    single = os.environ.get(
        "LLAMACPP_URL",
        "http://llama-cpp-server:5006/v1/completions",
    ).strip()
    _urls = [single]
    return _urls


def pick_completion_url() -> str:
    urls = completion_urls()
    if len(urls) == 1:
        return urls[0]
    global _rr
    with _lock:
        idx = _rr % len(urls)
        _rr += 1
    return urls[idx]


def probe_bases() -> list[str]:
    """HTTP origins to poll for readiness (LB or each backend)."""
    bases: list[str] = []
    for url in completion_urls():
        p = urlparse(url)
        port = p.port or (443 if p.scheme == "https" else 80)
        bases.append(f"{p.scheme}://{p.hostname}:{port}")
    seen: set[str] = set()
    out: list[str] = []
    for b in bases:
        if b not in seen:
            seen.add(b)
            out.append(b)
    return out
