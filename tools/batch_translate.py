"""Translation batch protocol (pipeline v2, ticket 04).

Batches are packed by an explicit token/character budget, sent as a numbered JSON
array request, validated for count, completeness and non-empty content, retried on
protocol failure, and finally recovered with one call per segment. Every segment
ends with a target or a recorded failure, and results are written back by segment
identity without reordering state.
"""

from __future__ import annotations

import json
import math
import re
from typing import Any, Callable

DEFAULT_BATCH_BUDGET = 2000
MAX_PROTOCOL_RETRIES = 2

Measure = Callable[[str], int]


class ProtocolError(ValueError):
    """Raised when model output does not satisfy the batch protocol."""


def estimate_tokens(text: str) -> int:
    """Estimate model tokens without a tokenizer.

    Non-ASCII characters (CJK and punctuation) count as one token each; ASCII
    characters count as one token per four characters, rounded up.
    """
    if not text:
        return 0
    ascii_chars = sum(1 for char in text if ord(char) < 128)
    other_chars = len(text) - ascii_chars
    return math.ceil(ascii_chars / 4) + other_chars


def plan_batch(
    segments: list[dict[str, Any]],
    budget: int,
    measure: Measure | None = None,
) -> list[list[dict[str, Any]]]:
    """Pack segments into batches that never exceed the budget.

    The budget is expressed in units returned by ``measure`` (tokens by default).
    Segment order is preserved. A single segment larger than the budget is rejected
    here; splitting oversized segments belongs to the long-segment ticket.
    """
    if budget <= 0:
        raise ValueError("batch budget must be positive")
    measure = measure or estimate_tokens
    batches: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    used = 0
    for segment in segments:
        size = measure(segment.get("source") or "")
        if size > budget:
            raise ValueError(
                f"segment {segment.get('index')} needs {size} units and exceeds "
                f"the batch budget of {budget}"
            )
        if current and used + size > budget:
            batches.append(current)
            current = []
            used = 0
        current.append(segment)
        used += size
    if current:
        batches.append(current)
    return batches


def build_batch_prompt(segments: list[dict[str, Any]]) -> str:
    """Build a numbered batch request for exactly one target per segment."""
    lines = [
        "Translate each numbered source segment into Simplified Chinese.",
        f"Return a JSON array of exactly {len(segments)} strings, one target per segment, "
        "in the same order.",
        "Do not add commentary, explanations, extra items or empty strings.",
        "",
    ]
    for segment in segments:
        lines.append(f"[{segment.get('index')}] {segment.get('source') or ''}")
    return "\n".join(lines)


def build_single_prompt(segment: dict[str, Any]) -> str:
    """Build a one-segment request for the fallback path."""
    return "\n".join(
        [
            "Translate the source segment into Simplified Chinese.",
            "Return a JSON array of exactly 1 string containing the target.",
            "Do not add commentary, explanations, extra items or empty strings.",
            "",
            f"[{segment.get('index')}] {segment.get('source') or ''}",
        ]
    )


def _call_client(client: Any, prompt: str) -> Any:
    """Invoke a callable client or an object exposing ``complete(prompt)``."""
    if callable(client):
        return client(prompt)
    complete = getattr(client, "complete", None)
    if callable(complete):
        return complete(prompt)
    raise TypeError("client must be callable or expose complete(prompt)")


def _parse_payload(text: Any) -> Any:
    if not isinstance(text, str):
        raise ProtocolError("model output is not text")
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```[A-Za-z]*\s*\n?", "", stripped)
        stripped = re.sub(r"\n?```\s*$", "", stripped)
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        match = re.search(r"(\[.*\]|\{.*\})", stripped, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                pass
        raise ProtocolError("model output is not valid JSON") from None


def _extract_targets(text: Any, expected: int) -> list[str]:
    """Validate the equal-length JSON array contract and return clean targets."""
    payload = _parse_payload(text)
    if isinstance(payload, dict):
        if payload.get("complete") is not True:
            raise ProtocolError("model output is marked incomplete")
        targets = payload.get("targets")
    elif isinstance(payload, list):
        targets = payload
    else:
        raise ProtocolError("model output is not a JSON array")
    if not isinstance(targets, list):
        raise ProtocolError("model output targets are not a JSON array")
    if len(targets) != expected:
        raise ProtocolError(f"expected {expected} targets, got {len(targets)}")

    cleaned: list[str] = []
    for position, item in enumerate(targets):
        target = item.get("target") if isinstance(item, dict) else item
        if not isinstance(target, str) or not target.strip():
            raise ProtocolError(f"target {position} is empty or not a string")
        cleaned.append(target.strip())
    return cleaned


def _result(
    segment: dict[str, Any],
    *,
    target: str | None,
    error: str | None,
    attempts: int,
    stage: str,
) -> dict[str, Any]:
    return {
        "index": segment.get("index"),
        "source_hash": segment.get("source_hash"),
        "target": target,
        "error": error,
        "attempts": attempts,
        "stage": stage,
    }


def _error_text(exc: Exception) -> str:
    return str(exc) or exc.__class__.__name__


def translate_batch(
    client: Any,
    segments: list[dict[str, Any]],
    *,
    budget: int = DEFAULT_BATCH_BUDGET,
    measure: Measure | None = None,
    max_retries: int = MAX_PROTOCOL_RETRIES,
) -> list[dict[str, Any]]:
    """Translate segments with an equal-length batch protocol.

    Each batch is attempted once plus up to ``max_retries`` additional attempts on
    protocol failure. If all attempts fail, every segment in the batch gets one
    fallback call. The returned list follows input order and contains one result per
    segment, with either a target or a recorded error.
    """
    segments = list(segments)
    if not segments:
        return []

    results: list[dict[str, Any]] = []
    for batch in plan_batch(segments, budget, measure):
        attempts = 0
        for _ in range(max_retries + 1):
            attempts += 1
            try:
                targets = _extract_targets(
                    _call_client(client, build_batch_prompt(batch)), len(batch)
                )
            except Exception:
                continue
            for segment, target in zip(batch, targets):
                results.append(
                    _result(segment, target=target, error=None, attempts=attempts, stage="batch")
                )
            break
        else:
            for segment in batch:
                try:
                    targets = _extract_targets(
                        _call_client(client, build_single_prompt(segment)), 1
                    )
                except Exception as exc:
                    results.append(
                        _result(
                            segment,
                            target=None,
                            error=_error_text(exc),
                            attempts=1,
                            stage="fallback",
                        )
                    )
                else:
                    results.append(
                        _result(
                            segment,
                            target=targets[0],
                            error=None,
                            attempts=1,
                            stage="fallback",
                        )
                    )

    return results


def apply_targets(chapter: dict[str, Any], results: list[dict[str, Any]]) -> dict[str, int]:
    """Write targets back to chapter segments by identity.

    Matching uses the segment index, and the source hash when the result carries
    one. A stale hash or an unknown index is skipped; a result without a non-empty
    target counts as a failure. Segment order is never changed.
    """
    segments = chapter.get("segments") or []
    by_index = {segment.get("index"): segment for segment in segments}
    applied = failed = skipped = 0
    for result in results:
        segment = by_index.get(result.get("index"))
        if segment is None:
            skipped += 1
            continue
        expected = result.get("source_hash")
        if expected and expected != segment.get("source_hash"):
            skipped += 1
            continue
        target = result.get("target")
        if isinstance(target, str) and target.strip():
            segment["target"] = target.strip()
            applied += 1
        else:
            failed += 1
    return {"applied": applied, "failed": failed, "skipped": skipped}
