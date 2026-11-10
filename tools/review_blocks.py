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

import batch_translate
import json_repair
from batch_translate import DEFAULT_BATCH_BUDGET
from model_client import call_client
from token_budget import estimate_tokens

REVIEW_BATCH_FACTOR = 3
DEFAULT_REVIEW_BUDGET = REVIEW_BATCH_FACTOR * DEFAULT_BATCH_BUDGET
DEFAULT_WORKERS = 1
DEFAULT_BISECT_DEPTH = 8
DEFAULT_SEGMENT_RETRIES = 2
DEFAULT_MAX_CALLS = 128
TRACE_RAW_CHARS = 12000


class ReviewProtocolError(ValueError):
    """Raised when review output does not satisfy the structured contract.

    ``raw`` keeps the model response that failed validation so callers can
    persist it for diagnosis instead of losing the evidence.
    """

    def __init__(self, message: str, raw: str | None = None) -> None:
        super().__init__(message)
        self.raw = raw


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
        "Each issue must include category and severity. category is one of omission, "
        "addition, mistranslation, semantic_reversal, consistency, terminology, style, "
        "other; severity is one of high, medium, low.",
        "Report only substantive defects. Omit segments with no issue; never emit an "
        "issue whose proposal is 'No change needed'.",
        f"reviewed_segments: the integer {count}, matching the number of segments below.",
        "complete: true.",
        "Do not translate, rewrite, edit or add commentary.",
        "",
    ]


def _glossary_text(terms: list[Any] | None) -> str:
    """Format relevant glossary terms for a review prompt."""
    lines: list[str] = []
    for term in terms or []:
        if not isinstance(term, dict):
            continue
        source = term.get("source") or term.get("id") or ""
        target = term.get("target") or ""
        if not source and not target:
            continue
        line = f"- {source}"
        if target:
            line += f" => {target}"
        aliases = [alias for alias in term.get("aliases") or [] if isinstance(alias, str)]
        if aliases:
            line += f" (aliases: {', '.join(aliases)})"
        lines.append(line)
    return "\n".join(lines)


def build_review_prompt(
    block: list[dict[str, Any]], rules: str | None = None, glossary: str | None = None
) -> str:
    """Build a numbered source/target review request for one block."""
    lines = [
        "TASK: REVIEW",
        "Review these numbered source/target segment pairs for sentence-level "
        "fidelity and cross-chapter consistency: omission, addition, mistranslation, "
        "semantic reversal, term and spell accuracy, name drift, pronoun and gender "
        "use, timeline and character state, premature spoilers, AU rule breaks and "
        "tone drift.",
    ]
    if isinstance(rules, str) and rules.strip():
        lines.extend(["PROFILE RULES (must follow):", rules.strip(), ""])
    if isinstance(glossary, str) and glossary.strip():
        lines.extend(["GLOSSARY (canonical targets, must be followed):", glossary.strip(), ""])
    lines.extend(_protocol_contract(len(block)))
    for segment in block:
        lines.append(f"[{segment.get('index')}]")
        lines.append(f"SOURCE: {segment.get('source') or ''}")
        lines.append(f"TARGET: {segment.get('target') or ''}")
    return "\n".join(lines)


_PARSE_FAILED = object()


def _strict_value(candidate: str) -> Any:
    try:
        return json.loads(candidate)
    except (json.JSONDecodeError, TypeError):
        return _PARSE_FAILED


def _same_json_value(left: Any, right: Any) -> bool:
    left_json = json.dumps(left, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    right_json = json.dumps(right, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return left_json == right_json


def _is_boundary_only_repair(raw: str, repaired_value: Any) -> bool:
    """Accept only repairs that expose an unchanged strict JSON value.

    A missing outer closing delimiter, surplus closing delimiters, or a JSON
    value inside a Markdown fence are safe. Completing strings, keys or array
    items is structural and is rejected because it could invent review content.
    """
    for delimiter in ("}", "]"):
        candidate = _strict_value(raw + delimiter)
        if candidate is not _PARSE_FAILED and _same_json_value(candidate, repaired_value):
            return True

    without_extra = raw
    while without_extra.endswith(("}", "]")):
        without_extra = without_extra[:-1].rstrip()
        candidate = _strict_value(without_extra)
        if candidate is not _PARSE_FAILED and _same_json_value(candidate, repaired_value):
            return True

    lines = raw.splitlines()
    if (
        len(lines) >= 3
        and lines[0].strip().lower() in {"```", "```json"}
        and lines[-1].strip() == "```"
    ):
        candidate = _strict_value("\n".join(lines[1:-1]).strip())
        if candidate is not _PARSE_FAILED and _same_json_value(candidate, repaired_value):
            return True
    return False


def _parse_json(text: Any) -> Any:
    if not isinstance(text, str):
        raise ReviewProtocolError("model output is not text")
    raw = text.strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    try:
        value = json_repair.repair_json(raw, return_objects=True, skip_json_loads=True)
    except Exception:
        raise ReviewProtocolError("model output is not valid JSON", raw=raw) from None
    if value == "":
        raise ReviewProtocolError("model output is not valid JSON", raw=raw)
    if not _is_boundary_only_repair(raw, value):
        raise ReviewProtocolError("model output needs structural JSON repair", raw=raw)
    return value


_NO_ISSUE_PROPOSAL = re.compile(r"^\s*(no change needed|no change|none|n/?a)\.?\s*$", re.I)
_NO_ISSUE_MESSAGE = re.compile(
    r"(?:^|[.!?。]\s*)(?:no issue|no defect|no problem)[.!?。]*\s*$"
    r"|(?:无问题|无需修改|不需要修改|无需改动|无需调整)[。.!?]*\s*$",
    re.I,
)


def _is_no_issue(issue: dict[str, Any]) -> bool:
    """Reject model entries that explicitly say the segment needs no change."""
    proposal = (issue.get("proposal") or "").strip()
    message = (issue.get("message") or "").strip()
    if proposal and _NO_ISSUE_PROPOSAL.match(proposal):
        return True
    return bool(_NO_ISSUE_MESSAGE.search(message))


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
        if _is_no_issue(issue):
            continue
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


def _clip_raw(raw: Any) -> str:
    text = raw if isinstance(raw, str) else str(raw)
    if len(text) <= TRACE_RAW_CHARS:
        return text
    return text[:TRACE_RAW_CHARS] + "\n…[truncated]"


def _review_once(
    client: Any,
    block: list[dict[str, Any]],
    rules: str | None = None,
    glossary: str | None = None,
) -> dict[str, Any]:
    allowed = {segment.get("index") for segment in block}
    raw = call_client(client, build_review_prompt(block, rules=rules, glossary=glossary))
    try:
        return validate_review_output(raw, len(block), allowed)
    except ReviewProtocolError as exc:
        if exc.raw is None:
            exc.raw = raw if isinstance(raw, str) else str(raw)
        raise


class _CallBudget:
    """Bound total model calls for one review block, not just recursion depth."""

    def __init__(self, max_calls: int | None) -> None:
        self.max_calls = max_calls
        self.calls = 0

    def take(self) -> bool:
        if self.max_calls is not None and self.calls >= self.max_calls:
            return False
        self.calls += 1
        return True


def _review_leaf(
    client: Any,
    block: list[dict[str, Any]],
    depth: int,
    max_depth: int,
    max_segment_retries: int,
    rules: str | None = None,
    trace=None,
    budget: _CallBudget | None = None,
    glossary: str | None = None,
) -> list[dict[str, Any]]:
    """Review one block, bisecting protocol failures down to single segments."""
    if len(block) == 1:
        attempts = 0
        last_error = "review output was rejected"
        for attempt in range(max_segment_retries + 1):
            attempts += 1
            if budget is not None and not budget.take():
                return [_failure(block, "review call budget exhausted", attempts=0, depth=depth)]
            try:
                payload = _review_once(client, block, rules, glossary)
            except ReviewProtocolError as exc:
                last_error = _error_text(exc)
                if trace is not None:
                    trace(
                        {
                            "event": "rejected",
                            "depth": depth,
                            "segments": _indices(block),
                            "attempt": attempts,
                            "error": last_error,
                            "raw": _clip_raw(exc.raw),
                        }
                    )
                continue
            if trace is not None:
                trace(
                    {
                        "event": "accepted",
                        "depth": depth,
                        "segments": _indices(block),
                        "attempt": attempts,
                    }
                )
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
                    rules,
                    trace,
                    budget,
                    glossary,
                )
            )
        return leaves

    attempts = 1
    if budget is not None and not budget.take():
        return [_failure(block, "review call budget exhausted", attempts=0, depth=depth)]
    try:
        payload = _review_once(client, block, rules, glossary)
    except ReviewProtocolError as exc:
        if trace is not None:
            trace(
                {
                    "event": "rejected",
                    "depth": depth,
                    "segments": _indices(block),
                    "attempt": attempts,
                    "error": _error_text(exc),
                    "raw": _clip_raw(exc.raw),
                }
            )
        midpoint = len(block) // 2
        return _review_leaf(
            client,
            block[:midpoint],
            depth + 1,
            max_depth,
            max_segment_retries,
            rules,
            trace,
            budget,
            glossary,
        ) + _review_leaf(
            client,
            block[midpoint:],
            depth + 1,
            max_depth,
            max_segment_retries,
            rules,
            trace,
            budget,
            glossary,
        )
    if trace is not None:
        trace(
            {
                "event": "accepted",
                "depth": depth,
                "segments": _indices(block),
                "attempt": attempts,
            }
        )
    return [_success(block, payload, attempts=attempts, depth=depth)]


def review_block(
    client: Any,
    block: list[dict[str, Any]],
    *,
    max_bisect_depth: int = DEFAULT_BISECT_DEPTH,
    max_segment_retries: int = DEFAULT_SEGMENT_RETRIES,
    max_calls: int | None = DEFAULT_MAX_CALLS,
    rules: str | None = None,
    glossary: str | None = None,
    trace=None,
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

    budget = _CallBudget(max_calls)
    parts = _review_leaf(
        client,
        block,
        0,
        max_bisect_depth,
        max_segment_retries,
        rules=rules,
        trace=trace,
        budget=budget,
        glossary=glossary,
    )
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


CACHE_VERSION = 5


def block_cache_key(
    block: list[dict[str, Any]], rules: str | None = None, glossary: str | None = None
) -> str:
    rules_hash = hashlib.sha256((rules or "").encode("utf-8")).hexdigest()
    glossary_hash = hashlib.sha256((glossary or "").encode("utf-8")).hexdigest()
    payload = [
        CACHE_VERSION,
        rules_hash,
        glossary_hash,
        *[
            (segment.get("index"), segment.get("source_hash"), segment.get("target"))
            for segment in block
        ],
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
    max_calls: int | None = DEFAULT_MAX_CALLS,
    measure=None,
    cache_dir: Path | None = None,
    trace_dir: Path | None = None,
    rules: str | None = None,
    glossary_terms: list[Any] | None = None,
    limit_blocks: int | None = None,
) -> list[dict[str, Any]]:
    """Plan and review blocks in parallel, returning results in block order.

    When ``cache_dir`` is given, a complete block result is written by block
    identity and reused on a later run; invalid blocks are not cached. When
    ``trace_dir`` is given, every block's recovery trace (including raw model
    responses that failed validation) is written there for diagnosis.
    """
    if workers < 1:
        raise ValueError("workers must be at least 1")
    blocks = plan_blocks(segments, budget, measure)
    if limit_blocks is not None:
        if limit_blocks < 1:
            raise ValueError("limit_blocks must be positive")
        blocks = blocks[:limit_blocks]
    if not blocks:
        return []
    if cache_dir is not None:
        cache_dir.mkdir(parents=True, exist_ok=True)
    if trace_dir is not None:
        trace_dir.mkdir(parents=True, exist_ok=True)

    def review_one(block: list[dict[str, Any]]) -> dict[str, Any]:
        glossary = _glossary_text(
            batch_translate.relevant_glossary_terms(block, glossary_terms)
        )
        key = block_cache_key(block, rules, glossary)
        cache_path = cache_dir / f"{key}.json" if cache_dir is not None else None
        if cache_path is not None:
            cached = _read_cache(cache_path)
            if cached is not None:
                return cached
        events: list[dict[str, Any]] = []
        result = review_block(
            client,
            block,
            max_bisect_depth=max_bisect_depth,
            max_segment_retries=max_segment_retries,
            max_calls=max_calls,
            rules=rules,
            glossary=glossary,
            trace=events.append,
        )
        if trace_dir is not None:
            trace_path = trace_dir / f"{key}.json"
            trace_path.write_text(
                json.dumps(
                    {"segments": _indices(block), "events": events},
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
        if cache_path is not None and result.get("complete") is True:
            cache_path.write_text(
                json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
        return result

    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(review_one, blocks))
