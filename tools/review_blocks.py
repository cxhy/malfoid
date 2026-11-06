"""Structured review blocks (pipeline v2, ticket 08).

Review packs contiguous segments into blocks sized to roughly three translation
batches, runs blocks in parallel, and accepts only structured JSON output with
``issues``, ``reviewed_segments`` and ``complete``. Invalid output bisects the
block within a configured depth bound; blocks that reach the bound fall back to
one call per segment with bounded per-segment retries. Review stays advisory and
returns suggestions only.
"""

from __future__ import annotations

import hashlib
import json
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from batch_translate import DEFAULT_BATCH_BUDGET
from model_client import call_client
from token_budget import estimate_tokens

REVIEW_BATCH_FACTOR = 3
DEFAULT_REVIEW_BUDGET = REVIEW_BATCH_FACTOR * DEFAULT_BATCH_BUDGET
DEFAULT_WORKERS = 2
DEFAULT_BISECT_DEPTH = 2
DEFAULT_SEGMENT_RETRIES = 1


class ReviewProtocolError(ValueError):
    """Raised when review output does not satisfy the structured contract."""


def plan_blocks(
    segments: list[dict[str, Any]],
    budget: int = DEFAULT_REVIEW_BUDGET,
    measure=None,
) -> list[list[dict[str, Any]]]:
    """Pack contiguous segments into deterministic blocks within the budget.

    A segment larger than the budget is rejected here; the long-segment splitter
    keeps individual segments below the translation batch budget.
    """
    if budget <= 0:
        raise ValueError("review block budget must be positive")
    measure = measure or estimate_tokens
    blocks: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    used = 0
    for segment in segments:
        size = measure(segment.get("source") or "") + measure(segment.get("target") or "")
        if size > budget:
            raise ValueError(
                f"segment {segment.get('index')} needs {size} units and exceeds "
                f"the review block budget of {budget}"
            )
        if current and used + size > budget:
            blocks.append(current)
            current = []
            used = 0
        current.append(segment)
        used += size
    if current:
        blocks.append(current)
    return blocks


def _protocol_contract(count: int) -> list[str]:
    return [
        "Return a JSON object with exactly three keys: issues, reviewed_segments and complete.",
        "issues: an array of objects; each has an integer segment_index and a non-empty message.",
        "Each issue may also include optional subject and proposal strings: subject groups "
        "related issues across blocks, and proposal states the recommended fix.",
        f"reviewed_segments: the integer {count}, matching the number of segments below.",
        "complete: true.",
        "Do not translate, rewrite, edit or add commentary.",
        "",
    ]


def build_review_prompt(block: list[dict[str, Any]]) -> str:
    """Build a numbered source/target review request for one block."""
    lines = [
        "TASK: REVIEW",
        "Review these numbered source/target segment pairs for cross-chapter "
        "consistency: term and name drift, pronoun and gender use, timeline and "
        "character state, premature spoilers, AU rule breaks and tone drift.",
        *_protocol_contract(len(block)),
    ]
    for segment in block:
        lines.append(f"[{segment.get('index')}]")
        lines.append(f"SOURCE: {segment.get('source') or ''}")
        lines.append(f"TARGET: {segment.get('target') or ''}")
    return "\n".join(lines)


def _parse_json(text: Any) -> Any:
    if not isinstance(text, str):
        raise ReviewProtocolError("model output is not text")
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
        raise ReviewProtocolError("model output is not valid JSON") from None


def validate_review_output(
    text: Any, expected_segments: int, allowed_indices: set[int] | None = None
) -> dict[str, Any]:
    """Validate one structured review payload and return a normalized copy.

    Accepts raw model text or an already parsed object. The payload must have
    ``complete: true``, an integer ``reviewed_segments`` equal to the block size,
    and an ``issues`` array whose entries carry an integer ``segment_index`` and
    a non-empty ``message``. When ``allowed_indices`` is given, every issue index
    must be inside the reviewed block.
    """
    payload = text if isinstance(text, dict) else _parse_json(text)
    if not isinstance(payload, dict):
        raise ReviewProtocolError("model output must be a JSON object")
    if payload.get("complete") is not True:
        raise ReviewProtocolError("model output is not marked complete")

    reviewed = payload.get("reviewed_segments")
    if isinstance(reviewed, bool) or not isinstance(reviewed, int):
        raise ReviewProtocolError("reviewed_segments must be an integer")
    if reviewed != expected_segments:
        raise ReviewProtocolError(f"expected {expected_segments} reviewed segments, got {reviewed}")

    issues = payload.get("issues")
    if not isinstance(issues, list):
        raise ReviewProtocolError("issues must be a JSON array")
    cleaned: list[dict[str, Any]] = []
    for position, issue in enumerate(issues):
        if not isinstance(issue, dict):
            raise ReviewProtocolError(f"issue {position} is not a JSON object")
        index = issue.get("segment_index")
        if isinstance(index, bool) or not isinstance(index, int):
            raise ReviewProtocolError(f"issue {position} is missing an integer segment_index")
        if allowed_indices is not None and index not in allowed_indices:
            raise ReviewProtocolError(f"issue {position} references a segment outside the block")
        message = issue.get("message")
        if not isinstance(message, str) or not message.strip():
            raise ReviewProtocolError(f"issue {position} is missing a non-empty message")
        for field in ("subject", "proposal"):
            if field not in issue:
                continue
            value = issue.get(field)
            if not isinstance(value, str) or not value.strip():
                raise ReviewProtocolError(f"issue {position} has an invalid {field}")
        cleaned.append(issue)
    return {"issues": cleaned, "reviewed_segments": reviewed, "complete": True}


def _error_text(exc: Exception) -> str:
    return str(exc) or exc.__class__.__name__


def _issue_key(issue: dict[str, Any]) -> int:
    return int(issue.get("segment_index") or 0)


def _indices(block: list[dict[str, Any]]) -> list[Any]:
    return [segment.get("index") for segment in block]


def _success(
    block: list[dict[str, Any]],
    payload: dict[str, Any],
    *,
    attempts: int,
    depth: int,
) -> dict[str, Any]:
    return {
        "segments": _indices(block),
        "start": block[0].get("index"),
        "end": block[-1].get("index"),
        "issues": sorted(payload["issues"], key=_issue_key),
        "reviewed_segments": payload["reviewed_segments"],
        "complete": True,
        "attempts": attempts,
        "stage": "segment" if len(block) == 1 else "block",
        "depth": depth,
        "error": None,
    }


def _failure(
    block: list[dict[str, Any]],
    error: str,
    *,
    attempts: int,
    depth: int,
) -> dict[str, Any]:
    return {
        "segments": _indices(block),
        "start": block[0].get("index"),
        "end": block[-1].get("index"),
        "issues": [],
        "reviewed_segments": 0,
        "complete": False,
        "attempts": attempts,
        "stage": "failed",
        "depth": depth,
        "error": error,
    }


def _review_once(client: Any, block: list[dict[str, Any]]) -> dict[str, Any]:
    allowed = {segment.get("index") for segment in block}
    return validate_review_output(
        call_client(client, build_review_prompt(block)), len(block), allowed
    )


def _review_leaf(
    client: Any,
    block: list[dict[str, Any]],
    depth: int,
    max_depth: int,
    max_segment_retries: int,
) -> list[dict[str, Any]]:
    """Review one block, bisecting protocol failures within the depth bound."""
    if len(block) == 1:
        attempts = 0
        last_error = "review output was rejected"
        for _ in range(max_segment_retries + 1):
            attempts += 1
            try:
                payload = _review_once(client, block)
            except ReviewProtocolError as exc:
                last_error = _error_text(exc)
                continue
            return [_success(block, payload, attempts=attempts, depth=depth)]
        return [_failure(block, last_error, attempts=attempts, depth=depth)]

    if depth >= max_depth:
        leaves: list[dict[str, Any]] = []
        for segment in block:
            leaves.extend(
                _review_leaf(
                    client,
                    [segment],
                    depth,
                    max_depth,
                    max_segment_retries,
                )
            )
        return leaves

    attempts = 1
    try:
        payload = _review_once(client, block)
    except ReviewProtocolError:
        midpoint = len(block) // 2
        return _review_leaf(
            client, block[:midpoint], depth + 1, max_depth, max_segment_retries
        ) + _review_leaf(client, block[midpoint:], depth + 1, max_depth, max_segment_retries)
    return [_success(block, payload, attempts=attempts, depth=depth)]


def review_block(
    client: Any,
    block: list[dict[str, Any]],
    *,
    max_bisect_depth: int = DEFAULT_BISECT_DEPTH,
    max_segment_retries: int = DEFAULT_SEGMENT_RETRIES,
) -> dict[str, Any]:
    """Review one planned block and return its structured, deterministic result.

    A rejected block is bisected until it succeeds, reaches ``max_bisect_depth``,
    or falls back to per-segment calls. ``parts`` records the leaf results so a
    caller can inspect which sub-block produced each issue.
    """
    block = list(block)
    if not block:
        raise ValueError("review block must contain at least one segment")
    if max_bisect_depth < 0:
        raise ValueError("max_bisect_depth must not be negative")
    if max_segment_retries < 0:
        raise ValueError("max_segment_retries must not be negative")

    parts = _review_leaf(client, block, 0, max_bisect_depth, max_segment_retries)
    issues: list[dict[str, Any]] = []
    for part in parts:
        issues.extend(part["issues"])
    issues.sort(key=_issue_key)
    reviewed = sum(part["reviewed_segments"] for part in parts)
    complete = bool(parts) and all(part["complete"] for part in parts)
    complete = complete and reviewed == len(block)
    errors = [part["error"] for part in parts if part["error"]]
    if complete:
        stage = "block" if len(parts) == 1 and parts[0]["depth"] == 0 else "bisected"
    else:
        stage = "failed"
    return {
        "segments": _indices(block),
        "start": block[0].get("index"),
        "end": block[-1].get("index"),
        "issues": issues,
        "reviewed_segments": reviewed,
        "complete": complete,
        "attempts": sum(part["attempts"] for part in parts),
        "stage": stage,
        "parts": parts,
        "error": "; ".join(errors) if errors else None,
    }


def block_cache_key(block: list[dict[str, Any]]) -> str:
    payload = [
        (segment.get("index"), segment.get("source_hash"), segment.get("target"))
        for segment in block
    ]
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _read_cache(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) and data.get("complete") is True else None


def run_review(
    client: Any,
    segments: list[dict[str, Any]],
    *,
    budget: int = DEFAULT_REVIEW_BUDGET,
    workers: int = DEFAULT_WORKERS,
    max_bisect_depth: int = DEFAULT_BISECT_DEPTH,
    max_segment_retries: int = DEFAULT_SEGMENT_RETRIES,
    measure=None,
    cache_dir: Path | None = None,
) -> list[dict[str, Any]]:
    """Plan and review blocks in parallel, returning results in block order.

    When ``cache_dir`` is given, a complete block result is written by block
    identity and reused on a later run; invalid blocks are not cached.
    """
    if workers < 1:
        raise ValueError("workers must be at least 1")
    blocks = plan_blocks(segments, budget, measure)
    if not blocks:
        return []
    if cache_dir is not None:
        cache_dir.mkdir(parents=True, exist_ok=True)

    def review_one(block: list[dict[str, Any]]) -> dict[str, Any]:
        cache_path = cache_dir / f"{block_cache_key(block)}.json" if cache_dir is not None else None
        if cache_path is not None:
            cached = _read_cache(cache_path)
            if cached is not None:
                return cached
        result = review_block(
            client,
            block,
            max_bisect_depth=max_bisect_depth,
            max_segment_retries=max_segment_retries,
        )
        if cache_path is not None and result.get("complete") is True:
            cache_path.write_text(
                json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
        return result

    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(review_one, blocks))
