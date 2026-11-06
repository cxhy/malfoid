"""Shared model-client boundary for the v2 pipeline.

The pipeline accepts either a callable or an object exposing ``complete(prompt)``.
Tests inject fake clients; production tools supply a real adapter.
"""

from __future__ import annotations

from typing import Any


def call_client(client: Any, prompt: str) -> Any:
    if callable(client):
        return client(prompt)
    complete = getattr(client, "complete", None)
    if callable(complete):
        return complete(prompt)
    raise TypeError("client must be callable or expose complete(prompt)")


def ask_text(client: Any, prompt: str) -> str:
    text = str(call_client(client, prompt)).strip()
    if not text:
        raise ValueError("model client returned empty text")
    return text
