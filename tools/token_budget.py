"""Deterministic token budget estimates shared by the v2 pipeline.

The project does not ship a model tokenizer. This estimator is stable and testable
offline: non-ASCII characters count as one token each; ASCII characters count as one
token per four characters.
"""

from __future__ import annotations

import math


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    ascii_chars = sum(1 for char in text if ord(char) < 128)
    other_chars = len(text) - ascii_chars
    return math.ceil(ascii_chars / 4) + other_chars
