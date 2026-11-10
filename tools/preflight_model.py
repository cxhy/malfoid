#!/usr/bin/env python3
"""Probe the model endpoint for concurrency before launching parallel jobs.

Usage:
    .venv/bin/python tools/preflight_model.py
    .venv/bin/python tools/preflight_model.py --effort none --max-tokens 64

Sends one small request, then two concurrent requests and two sequential
requests, and compares wall time. A serial endpoint (the common case for a
single local model) gains nothing from workers > 1 and will queue parallel
callers, so the report recommends the worker count.

The review runner calls :func:`recommend_workers` when ``--workers`` is not
given, so the probe result is passed to the review workers automatically.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor

from model_api import call, load_config

PROMPT = "Reply with exactly: OK"
PARALLEL_GAIN = 1.6


def _timed_call(base: str, key: str, model: str, effort: str, max_tokens: int) -> float:
    started = time.time()
    text, _ = call(base, key, model, PROMPT, effort, max_tokens)
    elapsed = time.time() - started
    if not str(text).strip():
        raise RuntimeError("probe returned empty text")
    return elapsed


def probe(
    model: str,
    effort: str = "none",
    max_tokens: int = 64,
    *,
    base: str | None = None,
    key: str | None = None,
) -> dict:
    """Measure single/concurrent/sequential latency and recommend workers."""
    if base is None or key is None:
        base, key = load_config()
    single = _timed_call(base, key, model, effort, max_tokens)

    started = time.time()
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: _timed_call(base, key, model, effort, max_tokens), range(2)))
    concurrent = time.time() - started

    started = time.time()
    for _ in range(2):
        _timed_call(base, key, model, effort, max_tokens)
    sequential = time.time() - started

    parallel_gain = sequential / concurrent if concurrent > 0 else 0
    if parallel_gain >= PARALLEL_GAIN:
        mode = "parallel"
        workers = 2
    else:
        mode = "serial"
        workers = 1
    return {
        "single_seconds": round(single, 2),
        "concurrent_seconds": round(concurrent, 2),
        "sequential_seconds": round(sequential, 2),
        "parallel_gain": round(parallel_gain, 2),
        "mode": mode,
        "recommended_workers": workers,
    }


def recommend_workers(
    model: str,
    effort: str = "none",
    max_tokens: int = 64,
    *,
    base: str | None = None,
    key: str | None = None,
) -> int:
    """Return the worker count recommended by the endpoint probe."""
    return int(probe(model, effort, max_tokens, base=base, key=key)["recommended_workers"])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--model",
        default=os.environ.get("MALFOID_MODEL", "DeepSeek-V4.1-Flash-1017-TensorFold-Zig"),
    )
    parser.add_argument("--effort", default="none")
    parser.add_argument("--max-tokens", type=int, default=64)
    args = parser.parse_args()
    print(json.dumps(probe(args.model, args.effort, args.max_tokens), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
