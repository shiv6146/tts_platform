"""Prompt formatting for llama.cpp / OpenAI-compatible completion API."""

from __future__ import annotations

import logging

log = logging.getLogger("inference.prompt")

DEFAULT_VOICE = "tara"
ENGLISH_VOICES = ["tara", "leah", "jess", "leo", "dan", "mia", "zac", "zoe"]


def format_prompt(prompt: str, voice: str = DEFAULT_VOICE) -> str:
    if voice not in ENGLISH_VOICES:
        log.warning("voice %s not in known list, using %s", voice, DEFAULT_VOICE)
        voice = DEFAULT_VOICE
    formatted = f"{voice}: {prompt}"
    return f"<|audio|>{formatted}<|eot_id|>"
