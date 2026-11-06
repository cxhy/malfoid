"""Translation batch protocol (pipeline v2, ticket 04).

Batches are packed by an explicit token budget, sent as a numbered request that
must return ``{"complete": true, "targets": [...]}`` with exactly one target per
segment, validated for count, completeness and non-empty content, retried on
protocol failure, and finally recovered with one call per segment. Every segment
ends with a target or a recorded failure, and results are written back by segment
identity without reordering state.
"""

from __future__ import annotations

import json
import re
from typing import Any

from model_client import call_client
from token_budget import estimate_tokens

DEFAULT_BATCH_BUDGET = 2000
MAX_PROTOCOL_RETRIES = 2


class ProtocolError(ValueError):
    """Raised when model output does not satisfy the batch protocol."""


def plan_batch(
    segments: list[dict[str, Any]],
    budget: int,
    measure=None,
) -> list[list[dict[str, Any]]]:
    """Pack segments into batches that never exceed the budget.

    A single segment larger than the budget is rejected here; splitting oversized
    segments belongs to the long-segment ticket.
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


def _protocol_preamble(count: int) -> list[str]:
    return [
        f"Return a JSON object with exactly two keys: complete (true) and targets "
        f"(an array of exactly {count} strings, one target per segment, in order).",
        "Do not add commentary, explanations, extra items or empty strings.",
        "",
    ]


def _term_parts(term: Any) -> tuple[str, str, list[str]]:
    if isinstance(term, dict):
        source = str(term.get("source") or "").strip()
        target = str(term.get("target") or "").strip()
        aliases = [str(alias).strip() for alias in term.get("aliases") or [] if str(alias).strip()]
        return source, target, aliases
    text = str(term).strip()
    return text, "", []


def relevant_glossary_terms(
    segments: list[dict[str, Any]], glossary_terms: list[Any] | None
) -> list[Any]:
    """Select glossary entries whose source or alias occurs in the batch text."""
    if not glossary_terms:
        return []
    haystack = " ".join(segment.get("source") or "" for segment in segments).casefold()
    selected: list[Any] = []
    for term in glossary_terms:
        source, target, aliases = _term_parts(term)
        if not source and not target:
            continue
        needles = [source, *aliases]
        if not any(needle and needle.casefold() in haystack for needle in needles):
            continue
        selected.append(term)
    return selected


def _context_lines(
    segments: list[dict[str, Any]],
    glossary_terms: list[Any] | None,
    context: str | None,
) -> list[str]:
    lines: list[str] = []
    terms = relevant_glossary_terms(segments, glossary_terms)
    if terms:
        lines.append("Glossary (use the listed target terms exactly):")
        for term in terms:
            source, target, aliases = _term_parts(term)
            line = f"- {source}"
            if target:
                line += f" => {target}"
            if aliases:
                line += f" (aliases: {', '.join(aliases)})"
            lines.append(line)
        lines.append("")
    if isinstance(context, str) and context.strip():
        lines.append("Neighbour context (read-only):")
        lines.append(context.strip())
        lines.append("")
    return lines


def build_batch_prompt(
    segments: list[dict[str, Any]],
    glossary_terms: list[Any] | None = None,
    context: str | None = None,
) -> str:
    """Build a numbered batch request with optional glossary and neighbour context."""
    lines = [
        "Translate each numbered source segment into Simplified Chinese.",
        *_context_lines(segments, glossary_terms, context),
        *_protocol_preamble(len(segments)),
    ]
    for segment in segments:
        lines.append(f"[{segment.get('index')}] {segment.get('source') or ''}")
    return "\n".join(lines)


def build_single_prompt(
    segment: dict[str, Any],
    glossary_terms: list[Any] | None = None,
    context: str | None = None,
) -> str:
    """Build a one-segment request for the fallback path."""
    lines = [
        "Translate the source segment into Simplified Chinese.",
        *_context_lines([segment], glossary_terms, context),
        *_protocol_preamble(1),
    ]
    lines.append(f"[{segment.get('index')}] {segment.get('source') or ''}")
    return "\n".join(lines)


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


def extract_targets(text: Any, expected: int) -> list[str]:
    """Validate the completeness/count/non-empty contract and return clean targets."""
    payload = _parse_payload(text)
    if not isinstance(payload, dict):
        raise ProtocolError("model output must be a JSON object")
    if payload.get("complete") is not True:
        raise ProtocolError("model output is marked incomplete")
    targets = payload.get("targets")
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


def _fallback(
    client: Any,
    segments: list[dict[str, Any]],
    glossary_terms: list[Any] | None = None,
    context: str | None = None,
) -> list[dict[str, Any]]:
    results = []
    for segment in segments:
        try:
            targets = extract_targets(
                call_client(
                    client,
                    build_single_prompt(segment, glossary_terms=glossary_terms, context=context),
                ),
                1,
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


def translate_batch(
    client: Any,
    segments: list[dict[str, Any]],
    *,
    budget: int = DEFAULT_BATCH_BUDGET,
    measure=None,
    max_retries: int = MAX_PROTOCOL_RETRIES,
    glossary_terms: list[Any] | None = None,
    context: str | None = None,
) -> list[dict[str, Any]]:
    """Translate segments with an equal-length completeness contract.

    Protocol failures are retried up to ``max_retries`` additional times. If the
    batch cannot be planned or all attempts fail, every segment gets one fallback
    call. Optional glossary terms and neighbour context are injected into every
    prompt; only glossary entries relevant to that batch are listed. The returned
    list follows input order and contains one result per segment.
    """
    segments = list(segments)
    if not segments:
        return []
    try:
        batches = plan_batch(segments, budget, measure)
    except ValueError:
        return _fallback(client, segments, glossary_terms, context)

    results: list[dict[str, Any]] = []
    for batch in batches:
        attempts = 0
        for _ in range(max_retries + 1):
            attempts += 1
            try:
                targets = extract_targets(
                    call_client(
                        client,
                        build_batch_prompt(batch, glossary_terms=glossary_terms, context=context),
                    ),
                    len(batch),
                )
            except ProtocolError:
                continue
            for segment, target in zip(batch, targets):
                results.append(
                    _result(segment, target=target, error=None, attempts=attempts, stage="batch")
                )
            break
        else:
            results.extend(_fallback(client, batch, glossary_terms, context))

    return results


def apply_targets(chapter: dict[str, Any], results: list[dict[str, Any]]) -> dict[str, int]:
    """Write targets back to chapter segments by identity.

    Identity is segment index plus source hash; a result without both is skipped.
    Segment order is never changed.
    """
    segments = chapter.get("segments") or []
    by_index = {segment.get("index"): segment for segment in segments}
    applied = failed = skipped = 0
    for result in results:
        segment = by_index.get(result.get("index"))
        expected = result.get("source_hash")
        if segment is None or not expected or expected != segment.get("source_hash"):
            skipped += 1
            continue
        target = result.get("target")
        if isinstance(target, str) and target.strip():
            segment["target"] = target.strip()
            applied += 1
        else:
            failed += 1
    return {"applied": applied, "failed": failed, "skipped": skipped}
