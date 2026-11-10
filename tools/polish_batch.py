"""Polish batch protocol (pipeline v2, ticket 05).

Polish follows the same equal-length JSON contract as translation: N input
segments must return ``{"complete": true, "targets": [...]}`` with exactly one
non-empty polished target per segment. Protocol failures retry, then fall back to
one call per segment. Results are written back by segment identity, so a rejected
or failed polish leaves the original translation untouched and never changes
segment order or paragraph count.
"""

from __future__ import annotations

from typing import Any, Callable

from batch_translate import (
    DEFAULT_BATCH_BUDGET,
    MAX_PROTOCOL_RETRIES,
    ProtocolError,
    apply_targets,
    extract_targets,
    relevant_glossary_terms,
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


def _rule_lines(rules: str | None) -> list[str]:
    if not isinstance(rules, str) or not rules.strip():
        return []
    return ["Polish rules:", rules.strip(), ""]


def _glossary_lines(
    segments: list[dict[str, Any]], glossary_terms: list[Any] | None
) -> list[str]:
    terms = relevant_glossary_terms(segments, glossary_terms)
    if not terms:
        return []
    lines = ["Glossary (keep these canonical targets):"]
    for term in terms:
        if not isinstance(term, dict):
            continue
        source = str(term.get("source") or term.get("id") or "")
        target = str(term.get("target") or "")
        if not source and not target:
            continue
        line = f"- {source}"
        if target:
            line += f" => {target}"
        aliases = [str(alias) for alias in term.get("aliases") or [] if str(alias).strip()]
        if aliases:
            line += f" (aliases: {', '.join(aliases)})"
        lines.append(line)
    lines.append("")
    return lines


def build_polish_prompt(
    segments: list[dict[str, Any]],
    rules: str | None = None,
    glossary_terms: list[Any] | None = None,
) -> str:
    """Build a numbered polish request for exactly one target per segment."""
    lines = [
        "Polish each numbered Chinese translation segment.",
        "Keep the meaning, characters, plot and tone unchanged.",
        *_rule_lines(rules),
        *_glossary_lines(segments, glossary_terms),
        *_protocol_preamble(len(segments)),
    ]
    for segment in segments:
        lines.append(f"[{segment.get('index')}] {segment.get('target') or ''}")
    return "\n".join(lines)


def build_single_polish_prompt(
    segment: dict[str, Any],
    rules: str | None = None,
    glossary_terms: list[Any] | None = None,
) -> str:
    """Build a one-segment polish request for the fallback path."""
    lines = [
        "Polish the Chinese translation segment.",
        "Keep the meaning, characters, plot and tone unchanged.",
        *_rule_lines(rules),
        *_glossary_lines([segment], glossary_terms),
        *_protocol_preamble(1),
    ]
    lines.append(f"[{segment.get('index')}] {segment.get('target') or ''}")
    return "\n".join(lines)


def validate_polish_output(text: Any, expected: int) -> list[str]:
    """Validate the completeness/count/non-empty contract for polished targets."""
    return extract_targets(text, expected)


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


def _fallback(
    client: Any,
    segments: list[dict[str, Any]],
    rules: str | None = None,
    glossary_terms: list[Any] | None = None,
) -> list[dict[str, Any]]:
    results = []
    for segment in segments:
        try:
            targets = validate_polish_output(
                call_client(
                    client,
                    build_single_polish_prompt(
                        segment, rules=rules, glossary_terms=glossary_terms
                    ),
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


def polish_batch(
    client: Any,
    segments: list[dict[str, Any]],
    *,
    budget: int = DEFAULT_BATCH_BUDGET,
    measure=None,
    max_retries: int = MAX_PROTOCOL_RETRIES,
    rules: str | None = None,
    glossary_terms: list[Any] | None = None,
    on_batch: Callable[[list[dict[str, Any]]], None] | None = None,
) -> list[dict[str, Any]]:
    """Polish translated segments with an equal-length completeness contract.

    Every input segment must already carry a non-empty target. Protocol failures
    are retried up to ``max_retries`` additional times. If the batch cannot be
    planned or all attempts fail, every segment gets one fallback call. The
    returned list follows input order and contains one result per segment.
    ``on_batch`` is called with each batch's results as soon as they are ready.
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
        results = _fallback(client, segments, rules, glossary_terms)
        if on_batch is not None:
            on_batch(results)
        return results

    results: list[dict[str, Any]] = []
    for batch in batches:
        batch_results: list[dict[str, Any]] = []
        attempts = 0
        for _ in range(max_retries + 1):
            attempts += 1
            try:
                targets = validate_polish_output(
                    call_client(
                        client,
                        build_polish_prompt(
                            batch, rules=rules, glossary_terms=glossary_terms
                        ),
                    ),
                    len(batch),
                )
            except ProtocolError:
                continue
            for segment, target in zip(batch, targets):
                batch_results.append(
                    _result(segment, target=target, error=None, attempts=attempts, stage="batch")
                )
            break
        else:
            batch_results.extend(_fallback(client, batch, rules, glossary_terms))
        results.extend(batch_results)
        if on_batch is not None:
            on_batch(batch_results)

    return results


def apply_polished(chapter: dict[str, Any], results: list[dict[str, Any]]) -> dict[str, int]:
    """Write polished targets back by segment identity, leaving failures intact."""
    return apply_targets(chapter, results)
