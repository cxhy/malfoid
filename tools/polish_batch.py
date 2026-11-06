"""Polish batch protocol (pipeline v2, ticket 05).

Polish follows the same equal-length JSON contract as translation: N input
segments must return ``{"complete": true, "targets": [...]}`` with exactly one
non-empty polished target per segment. Protocol failures retry, then fall back to
one call per segment. Results are written back by segment identity, so a rejected
or failed polish leaves the original translation untouched and never changes
segment order or paragraph count.
"""

from __future__ import annotations

from typing import Any

from batch_translate import (
    DEFAULT_BATCH_BUDGET,
    MAX_PROTOCOL_RETRIES,
    ProtocolError,
    _extract_targets,
    apply_targets,
)
from model_client import call_client
from token_budget import estimate_tokens


def _protocol_preamble(count: int) -> list[str]:
    return [
        f"Return a JSON object with exactly two keys: complete (true) and targets "
        f"(an array of exactly {count} strings, one polished target per segment, "
        f"in order).",
        "Do not add commentary, explanations, extra items or empty strings.",
        "Each target must remain one segment: do not merge or split segments.",
        "",
    ]


def build_polish_prompt(segments: list[dict[str, Any]]) -> str:
    """Build a numbered polish request for exactly one target per segment."""
    lines = [
        "Polish each numbered Chinese translation segment.",
        "Keep the meaning, characters, plot and tone unchanged.",
        *_protocol_preamble(len(segments)),
    ]
    for segment in segments:
        lines.append(f"[{segment.get('index')}] {segment.get('target') or ''}")
    return "\n".join(lines)


def build_single_polish_prompt(segment: dict[str, Any]) -> str:
    """Build a one-segment polish request for the fallback path."""
    lines = [
        "Polish the Chinese translation segment.",
        "Keep the meaning, characters, plot and tone unchanged.",
        *_protocol_preamble(1),
    ]
    lines.append(f"[{segment.get('index')}] {segment.get('target') or ''}")
    return "\n".join(lines)


def validate_polish_output(text: Any, expected: int) -> list[str]:
    """Validate the completeness/count/non-empty contract for polished targets."""
    return _extract_targets(text, expected)


def plan_polish_batches(
    segments: list[dict[str, Any]],
    budget: int,
    measure=None,
) -> list[list[dict[str, Any]]]:
    """Pack segments by the token size of their existing translation target."""
    if budget <= 0:
        raise ValueError("batch budget must be positive")
    measure = measure or estimate_tokens
    batches: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    used = 0
    for segment in segments:
        size = measure(segment.get("target") or "")
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
        "original": segment.get("target"),
        "error": error,
        "attempts": attempts,
        "stage": stage,
    }


def _error_text(exc: Exception) -> str:
    return str(exc) or exc.__class__.__name__


def _fallback(client: Any, segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    results = []
    for segment in segments:
        try:
            targets = validate_polish_output(
                call_client(client, build_single_polish_prompt(segment)), 1
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


def polish_batch(
    client: Any,
    segments: list[dict[str, Any]],
    *,
    budget: int = DEFAULT_BATCH_BUDGET,
    measure=None,
    max_retries: int = MAX_PROTOCOL_RETRIES,
) -> list[dict[str, Any]]:
    """Polish translated segments with an equal-length completeness contract.

    Every input segment must already carry a non-empty target. Protocol failures
    are retried up to ``max_retries`` additional times. If the batch cannot be
    planned or all attempts fail, every segment gets one fallback call. The
    returned list follows input order and contains one result per segment.
    """
    segments = list(segments)
    if not segments:
        return []
    for segment in segments:
        target = segment.get("target")
        if not isinstance(target, str) or not target.strip():
            raise ValueError(f"segment {segment.get('index')} has no translation to polish")

    try:
        batches = plan_polish_batches(segments, budget, measure)
    except ValueError:
        return _fallback(client, segments)

    results: list[dict[str, Any]] = []
    for batch in batches:
        attempts = 0
        for _ in range(max_retries + 1):
            attempts += 1
            try:
                targets = validate_polish_output(
                    call_client(client, build_polish_prompt(batch)), len(batch)
                )
            except ProtocolError:
                continue
            for segment, target in zip(batch, targets):
                results.append(
                    _result(segment, target=target, error=None, attempts=attempts, stage="batch")
                )
            break
        else:
            results.extend(_fallback(client, batch))

    return results


def apply_polished(chapter: dict[str, Any], results: list[dict[str, Any]]) -> dict[str, int]:
    """Write polished targets back by segment identity, leaving failures intact."""
    return apply_targets(chapter, results)
