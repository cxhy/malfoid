"""Review evidence verification and conflict arbitration (pipeline v2, ticket 09).

Candidate review issues are verified against bounded cross-chapter evidence, with
at most two verification rounds. Every evidence reference names a concrete chapter
and segment position. Contradictory proposals from different review blocks are
grouped into conflicts, arbitrated by the injected model boundary, and left flagged
for a human decision when the model cannot resolve them.

The layer is advisory. It reads chapter state and structured review block results
through their public interfaces and never writes formal text.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

import segment_state
from model_client import call_client

MAX_EVIDENCE_ROUNDS = 2
DEFAULT_EVIDENCE_ROUNDS = 2
DEFAULT_MAX_EVIDENCE_REFS = 6
DEFAULT_MAX_EVIDENCE_CHARS = 1600

_PROPER_NOUN_RE = re.compile(r"\b[A-Z][a-z][a-z'’-]*\b")
_QUOTED_RE = re.compile(r"[“\"「『]([^”\"」』\n]{1,40})[”\"」』]")
_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "but",
        "chapter",
        "he",
        "her",
        "his",
        "i",
        "it",
        "its",
        "mr",
        "mrs",
        "ms",
        "dr",
        "professor",
        "she",
        "that",
        "the",
        "their",
        "then",
        "these",
        "they",
        "this",
        "those",
        "we",
        "when",
        "you",
    }
)


class EvidenceProtocolError(ValueError):
    """Raised when evidence verification or arbitration output is unusable."""


def candidate_id(candidate: dict[str, Any]) -> str:
    """Return the stable identity of a candidate review issue."""
    existing = candidate.get("id")
    if existing:
        return str(existing)
    seed = [
        candidate.get("chapter"),
        candidate.get("segment_index"),
        candidate.get("block"),
        candidate.get("message") or "",
        candidate.get("proposal") or "",
    ]
    encoded = json.dumps(seed, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return "cand-" + hashlib.sha256(encoded).hexdigest()[:12]


def _chapter_number(chapter: dict[str, Any], position: int) -> int | float:
    number = chapter.get("chapter")
    return segment_state.chapter_number(number) if number is not None else position + 1


def _locate_anchor(
    candidate: dict[str, Any], chapters: list[dict[str, Any]]
) -> tuple[int | float, dict[str, Any], int, dict[str, Any]]:
    target_chapter = segment_state.chapter_number(candidate.get("chapter"))
    target_index = int(candidate.get("segment_index"))
    for position, chapter in enumerate(chapters):
        number = _chapter_number(chapter, position)
        if number != target_chapter:
            continue
        segments = chapter.get("segments") or []
        for segment_position, segment in enumerate(segments):
            if int(segment.get("index", -1)) == target_index:
                return number, chapter, segment_position, segment
    raise ValueError(
        f"candidate references chapter {target_chapter} segment {target_index}, "
        "but no such position exists in chapter state"
    )


def _evidence_ref(chapter: int | float, segment: dict[str, Any], reason: str) -> dict[str, Any]:
    index = int(segment.get("index", 0))
    return {
        "chapter": chapter,
        "segment_index": index,
        "ref": f"ch-{segment_state.chapter_slug(chapter)}:{index}",
        "kind": segment.get("kind"),
        "source": segment.get("source") or "",
        "target": segment.get("target"),
        "reason": reason,
    }


def _ref_size(ref: dict[str, Any]) -> int:
    return len(ref.get("source") or "") + len(ref.get("target") or "")


def _ref_key(ref: dict[str, Any]) -> tuple[int | float, int]:
    return segment_state.chapter_number(ref.get("chapter")), int(ref.get("segment_index"))


def _excluded_keys(exclude: list[dict[str, Any]] | None) -> set[tuple[int, int]]:
    keys: set[tuple[int, int]] = set()
    for ref in exclude or []:
        if isinstance(ref, dict) and "chapter" in ref and "segment_index" in ref:
            keys.add(_ref_key(ref))
    return keys


def _normalise_terms(terms: Any) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    if not isinstance(terms, list):
        return result
    for term in terms:
        if not isinstance(term, str):
            continue
        text = term.strip()
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(text)
    return result


def _derived_terms(candidate: dict[str, Any], anchor: dict[str, Any]) -> list[str]:
    text = " ".join(
        [
            anchor.get("source") or "",
            anchor.get("target") or "",
            candidate.get("message") or "",
            candidate.get("proposal") or "",
        ]
    )
    terms: list[str] = []
    for match in _PROPER_NOUN_RE.finditer(text):
        token = match.group(0)
        if token.casefold() not in _STOPWORDS:
            terms.append(token)
    for match in _QUOTED_RE.finditer(text):
        terms.append(match.group(1))
    return _normalise_terms(terms)


def _matched_term(source: str, target: str, terms: list[str]) -> str | None:
    haystacks = (source.casefold(), target.casefold())
    for term in terms:
        needle = term.casefold()
        if any(needle in haystack for haystack in haystacks):
            return term
    return None


def _glossary_variants(terms: list[Any] | None) -> list[tuple[str, str]]:
    """Return (term id, forbidden variant) pairs from glossary terms."""
    variants: list[tuple[str, str]] = []
    seen: set[str] = set()
    for term in terms or []:
        if not isinstance(term, dict):
            continue
        term_id = str(term.get("id") or term.get("source") or "")
        canonical = str(term.get("target") or "")
        for variant in term.get("forbidden_targets") or []:
            if not isinstance(variant, str) or not variant.strip():
                continue
            value = variant.strip()
            if canonical and value in canonical:
                continue
            key = value.casefold()
            if key in seen:
                continue
            seen.add(key)
            variants.append((term_id, value))
    return variants


def _glossary_text(terms: list[Any] | None) -> str:
    """Format glossary terms for an evidence prompt."""
    lines: list[str] = []
    for term in terms or []:
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
        forbidden = [str(value) for value in term.get("forbidden_targets") or [] if str(value).strip()]
        if forbidden:
            line += f" (forbidden: {', '.join(forbidden)})"
        lines.append(line)
    return "\n".join(lines)


def _deterministic_terminology_result(
    candidate: dict[str, Any],
    chapters: list[dict[str, Any]],
    glossary_terms: list[Any] | None,
) -> dict[str, Any] | None:
    """Confirm a terminology candidate when it hits a glossary forbidden variant.

    The glossary is project-authoritative for terminology, so an exact forbidden
    variant hit does not need a model verdict. This only applies to candidates
    explicitly categorized as ``terminology``; ordinary term suggestions still go
    through evidence verification.
    """
    if str(candidate.get("category") or "").strip().lower() != "terminology":
        return None
    variants = _glossary_variants(glossary_terms)
    if not variants:
        return None
    chapter, _, _, segment = _locate_anchor(candidate, chapters)
    text = "\n".join(
        [
            str(segment.get("target") or ""),
            str(candidate.get("message") or ""),
            str(candidate.get("proposal") or ""),
        ]
    )
    hit = next(((term_id, variant) for term_id, variant in variants if variant in text), None)
    if hit is None:
        return None
    term_id, variant = hit
    return {
        "candidate": candidate,
        "id": candidate_id(candidate),
        "status": "confirmed",
        "verified": True,
        "needs_human": False,
        "rounds": 0,
        "reason": (
            f"glossary variant {variant!r} for term {term_id!r} appears in the "
            "target or candidate text; deterministic confirmation"
        ),
        "requested_terms": [],
        "evidence": [
            _evidence_ref(chapter, segment, f"glossary forbidden variant: {variant}")
        ],
        "error": None,
        "deterministic": True,
    }


def select_evidence(
    candidate: dict[str, Any],
    chapters: list[dict[str, Any]],
    *,
    focus_terms: list[str] | None = None,
    max_refs: int = DEFAULT_MAX_EVIDENCE_REFS,
    max_chars: int = DEFAULT_MAX_EVIDENCE_CHARS,
    exclude: list[dict[str, Any]] | None = None,
    include_neighbours: bool = False,
) -> list[dict[str, Any]]:
    """Select bounded evidence references for one candidate issue.

    The anchor segment is always included first. Focus-term occurrences in other
    chapters come next, ordered by chapter distance and position. ``max_refs`` and
    ``max_chars`` bound the additional references; the anchor counts toward both
    bounds but is never dropped. ``exclude`` omits references already used in an
    earlier round, and ``include_neighbours`` adds adjacent anchor-chapter segments
    when a later round needs local context.
    """
    if max_refs < 1:
        raise ValueError("max_refs must be positive")
    if max_chars < 1:
        raise ValueError("max_chars must be positive")

    anchor_chapter, anchor_chapter_state, anchor_position, anchor_segment = _locate_anchor(
        candidate, chapters
    )
    anchor_index = int(anchor_segment.get("index", 0))
    anchor_key = (anchor_chapter, anchor_index)
    excluded = _excluded_keys(exclude)

    refs: list[dict[str, Any]] = []
    used_chars = 0
    if anchor_key not in excluded:
        anchor_ref = _evidence_ref(anchor_chapter, anchor_segment, "candidate anchor")
        refs.append(anchor_ref)
        used_chars += _ref_size(anchor_ref)

    terms = _normalise_terms(
        focus_terms if focus_terms is not None else candidate.get("focus_terms")
    )
    if not terms:
        terms = _derived_terms(candidate, anchor_segment)

    occurrences: list[tuple[int, int, int, int, str]] = []
    for position, chapter in enumerate(chapters):
        number = _chapter_number(chapter, position)
        for segment in chapter.get("segments") or []:
            index = int(segment.get("index", -1))
            key = (number, index)
            if key == anchor_key or key in excluded:
                continue
            matched = _matched_term(segment.get("source") or "", segment.get("target") or "", terms)
            if matched is None:
                continue
            occurrences.append(
                (
                    0 if number != anchor_chapter else 1,
                    abs(number - anchor_chapter),
                    number,
                    index,
                    matched,
                )
            )
    occurrences.sort(key=lambda item: item[:4])

    for _, _, number, index, matched in occurrences:
        if len(refs) >= max_refs:
            break
        segment = _segment_at(chapters, number, index)
        if segment is None:
            continue
        ref = _evidence_ref(number, segment, f"focus term: {matched}")
        size = _ref_size(ref)
        if used_chars + size > max_chars:
            continue
        refs.append(ref)
        used_chars += size

    if include_neighbours:
        for neighbour_position in (anchor_position - 1, anchor_position + 1):
            if len(refs) >= max_refs:
                break
            segments = anchor_chapter_state.get("segments") or []
            if not (0 <= neighbour_position < len(segments)):
                continue
            segment = segments[neighbour_position]
            index = int(segment.get("index", -1))
            key = (anchor_chapter, index)
            if key == anchor_key or key in excluded or key in {_ref_key(ref) for ref in refs}:
                continue
            ref = _evidence_ref(anchor_chapter, segment, "adjacent context")
            size = _ref_size(ref)
            if used_chars + size > max_chars:
                continue
            refs.append(ref)
            used_chars += size

    return refs


def _segment_at(
    chapters: list[dict[str, Any]], chapter_number: int, segment_index: int
) -> dict[str, Any] | None:
    for position, chapter in enumerate(chapters):
        if _chapter_number(chapter, position) != chapter_number:
            continue
        for segment in chapter.get("segments") or []:
            if int(segment.get("index", -1)) == segment_index:
                return segment
    return None


def build_evidence_prompt(
    candidate: dict[str, Any],
    evidence: list[dict[str, Any]],
    round_number: int,
    max_rounds: int,
    glossary: str | None = None,
) -> str:
    """Build one evidence verification request."""
    lines = [
        "TASK: VERIFY_REVIEW_ISSUE",
        f"Round: {round_number} of {max_rounds}",
        "Verify one candidate review issue against the bounded cross-chapter evidence below.",
        "Confirm only when the evidence supports the candidate; reject it when it does not.",
        "Ask for more evidence when the references cannot decide the candidate.",
        "",
        f"Candidate: {candidate_id(candidate)}",
        f"Chapter: {candidate.get('chapter')}",
        f"Segment: {candidate.get('segment_index')}",
        f"Message: {candidate.get('message') or ''}",
        f"Proposal: {candidate.get('proposal') or ''}",
    ]
    if isinstance(glossary, str) and glossary.strip():
        lines.extend(["GLOSSARY (canonical targets):", glossary.strip(), ""])
    lines.append("Evidence:")
    for ref in evidence:
        lines.append(
            f"[{ref['ref']}] chapter={ref['chapter']} "
            f"segment={ref['segment_index']} reason={ref['reason']}"
        )
        lines.append(f"SOURCE: {ref.get('source') or ''}")
        lines.append(f"TARGET: {ref.get('target') or ''}")
    lines.extend(
        [
            "",
            "Return a JSON object with keys: verdict, reason, requested_terms.",
            'verdict: "confirmed", "rejected" or "need_more".',
            "reason: a non-empty string.",
            "requested_terms: an array of strings; empty unless verdict is need_more.",
        ]
    )
    return "\n".join(lines)


def _parse_json(text: Any) -> Any:
    if isinstance(text, dict):
        return text
    if not isinstance(text, str):
        raise EvidenceProtocolError("model output is not text")
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
        raise EvidenceProtocolError("model output is not valid JSON") from None


def validate_evidence_output(text: Any) -> dict[str, Any]:
    """Validate one evidence verification payload and return a normalized copy."""
    payload = _parse_json(text)
    if not isinstance(payload, dict):
        raise EvidenceProtocolError("model output must be a JSON object")

    verdict = payload.get("verdict")
    if verdict not in {"confirmed", "rejected", "need_more"}:
        raise EvidenceProtocolError('verdict must be "confirmed", "rejected" or "need_more"')

    reason = payload.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        raise EvidenceProtocolError("reason must be a non-empty string")

    requested = payload.get("requested_terms", [])
    if not isinstance(requested, list):
        raise EvidenceProtocolError("requested_terms must be a JSON array")
    requested_terms: list[str] = []
    for position, term in enumerate(requested):
        if not isinstance(term, str) or not term.strip():
            raise EvidenceProtocolError(f"requested_terms[{position}] must be a non-empty string")
        requested_terms.append(term.strip())

    return {
        "verdict": verdict,
        "reason": reason.strip(),
        "requested_terms": requested_terms,
    }


def _remaining_chars(refs: list[dict[str, Any]], max_chars: int) -> int:
    return max(0, max_chars - sum(_ref_size(ref) for ref in refs))


def verify_candidate(
    client: Any,
    candidate: dict[str, Any],
    chapters: list[dict[str, Any]],
    *,
    max_rounds: int = DEFAULT_EVIDENCE_ROUNDS,
    max_refs: int = DEFAULT_MAX_EVIDENCE_REFS,
    max_chars: int = DEFAULT_MAX_EVIDENCE_CHARS,
    focus_terms: list[str] | None = None,
    glossary_terms: list[Any] | None = None,
) -> dict[str, Any]:
    """Verify one candidate against evidence, with at most two rounds.

    A confirmed candidate is reported as verified, a rejected one is not, and a
    candidate that still needs evidence after the final round, or whose model
    output is unusable, is marked unresolved for a human decision. Terminology
    candidates that hit a glossary forbidden variant are confirmed deterministically
    without a model call.
    """
    if not 1 <= max_rounds <= MAX_EVIDENCE_ROUNDS:
        raise ValueError(f"max_rounds must be between 1 and {MAX_EVIDENCE_ROUNDS}")

    candidate = dict(candidate)
    deterministic = _deterministic_terminology_result(candidate, chapters, glossary_terms)
    if deterministic is not None:
        return deterministic

    evidence = select_evidence(
        candidate,
        chapters,
        focus_terms=focus_terms,
        max_refs=max_refs,
        max_chars=max_chars,
    )
    glossary = _glossary_text(glossary_terms)

    status = "unresolved"
    reason = ""
    error: str | None = None
    requested_terms: list[str] = []
    rounds = 0

    for round_number in range(1, max_rounds + 1):
        rounds = round_number
        prompt = build_evidence_prompt(candidate, evidence, round_number, max_rounds, glossary)
        try:
            payload = validate_evidence_output(call_client(client, prompt))
        except Exception as exc:
            status = "unresolved"
            error = str(exc) or exc.__class__.__name__
            reason = error
            break

        if payload["verdict"] == "need_more":
            reason = payload["reason"]
            requested_terms = payload["requested_terms"]
            if round_number >= max_rounds:
                status = "unresolved"
                break
            remaining_refs = max_refs - len(evidence)
            remaining_chars = _remaining_chars(evidence, max_chars)
            if remaining_refs > 0 and remaining_chars > 0:
                extra = select_evidence(
                    candidate,
                    chapters,
                    focus_terms=requested_terms or focus_terms,
                    max_refs=remaining_refs,
                    max_chars=remaining_chars,
                    exclude=evidence,
                    include_neighbours=True,
                )
                evidence.extend(extra)
            continue

        status = "confirmed" if payload["verdict"] == "confirmed" else "rejected"
        reason = payload["reason"]
        break

    return {
        "candidate": candidate,
        "id": candidate_id(candidate),
        "status": status,
        "verified": status == "confirmed",
        "needs_human": status == "unresolved",
        "rounds": rounds,
        "reason": reason,
        "requested_terms": requested_terms,
        "evidence": evidence,
        "error": error,
    }


def verify_candidates(
    client: Any,
    candidates: list[dict[str, Any]],
    chapters: list[dict[str, Any]],
    *,
    max_rounds: int = DEFAULT_EVIDENCE_ROUNDS,
    max_refs: int = DEFAULT_MAX_EVIDENCE_REFS,
    max_chars: int = DEFAULT_MAX_EVIDENCE_CHARS,
    glossary_terms: list[Any] | None = None,
) -> list[dict[str, Any]]:
    """Verify candidates in their original order."""
    return [
        verify_candidate(
            client,
            candidate,
            chapters,
            max_rounds=max_rounds,
            max_refs=max_refs,
            max_chars=max_chars,
            glossary_terms=glossary_terms,
        )
        for candidate in candidates
    ]


def _block_segments(
    block: list[dict[str, Any]] | dict[str, Any],
) -> tuple[list[dict[str, Any]], int | None]:
    if isinstance(block, dict):
        segments = list(block.get("segments") or [])
        chapter = block.get("chapter")
        return (
            segments,
            segment_state.chapter_number(chapter) if chapter is not None else None,
        )
    return list(block or []), None


def collect_candidates(
    reviews: list[dict[str, Any]],
    blocks: list[list[dict[str, Any]] | dict[str, Any]],
) -> list[dict[str, Any]]:
    """Turn review block results into chapter-aware candidate issues.

    Each block must carry chapter context either on its segments (``chapter``) or
    on the block itself. Segment indices inside one block must not be ambiguous
    across chapters.
    """
    if len(reviews) != len(blocks):
        raise ValueError("reviews and blocks must have the same length")

    candidates: list[dict[str, Any]] = []
    for block_index, (review, block) in enumerate(zip(reviews, blocks)):
        segments, default_chapter = _block_segments(block)
        chapter_by_index: dict[int, int | float] = {}
        for segment in segments:
            index = segment.get("index")
            chapter = segment.get("chapter", default_chapter)
            if index is None or chapter is None:
                continue
            index = int(index)
            chapter = segment_state.chapter_number(chapter)
            if index in chapter_by_index and chapter_by_index[index] != chapter:
                raise ValueError(
                    f"block {block_index} has ambiguous segment index {index} across chapters"
                )
            chapter_by_index[index] = chapter

        for issue in review.get("issues") or []:
            index = int(issue.get("segment_index"))
            if index not in chapter_by_index:
                raise ValueError(f"review issue references segment {index} without chapter context")
            message = issue.get("message") or ""
            proposal = issue.get("proposal")
            if not isinstance(proposal, str) or not proposal.strip():
                proposal = message
            candidate: dict[str, Any] = {
                "chapter": chapter_by_index[index],
                "segment_index": index,
                "message": message,
                "proposal": proposal,
                "block": block_index,
            }
            for field in ("subject", "conflict_key", "category", "severity"):
                if issue.get(field):
                    candidate[field] = issue[field]
            focus_terms = issue.get("focus_terms")
            if isinstance(focus_terms, list):
                candidate["focus_terms"] = list(focus_terms)
            candidate["id"] = candidate_id(candidate)
            candidates.append(candidate)
    return candidates


def _as_candidate(item: dict[str, Any]) -> dict[str, Any]:
    nested = item.get("candidate")
    return nested if isinstance(nested, dict) else item


def _block_label(candidate: dict[str, Any]) -> str:
    for field in ("block", "block_id"):
        if candidate.get(field) is not None:
            return str(candidate[field])
    return str(candidate.get("id") or "")


def _conflict_key(candidate: dict[str, Any]) -> str:
    if candidate.get("conflict_key"):
        return str(candidate["conflict_key"])
    if candidate.get("subject"):
        return f"subject:{candidate['subject']}"
    chapter = segment_state.chapter_number(candidate.get("chapter"))
    segment = int(candidate.get("segment_index"))
    return f"ch{segment_state.chapter_slug(chapter)}:s{segment}"


def _proposal_text(candidate: dict[str, Any]) -> str:
    proposal = candidate.get("proposal")
    if not isinstance(proposal, str) or not proposal.strip():
        proposal = candidate.get("message") or ""
    return proposal.strip()


def group_conflicts(
    candidates: list[dict[str, Any]], *, verified_only: bool = False
) -> list[dict[str, Any]]:
    """Group contradictory proposals from different blocks into conflicts.

    Raw candidates and verification records are both accepted. By default every
    verification record participates, so contradictory proposals still surface
    when evidence is rejected or unresolved. Set ``verified_only=True`` to skip
    records that are not confirmed. A conflict needs at least two candidates, two
    distinct blocks and two distinct proposal texts. Grouping uses
    ``conflict_key``, then ``subject``, then the concrete chapter/segment position.
    """
    groups: dict[str, list[tuple[dict[str, Any], dict[str, Any]]]] = {}
    for item in candidates:
        if not isinstance(item, dict):
            raise ValueError("candidate must be a dict")
        if verified_only and "status" in item and item.get("status") != "confirmed":
            continue
        candidate = _as_candidate(item)
        groups.setdefault(_conflict_key(candidate), []).append((item, candidate))

    conflicts: list[dict[str, Any]] = []
    for key in sorted(groups):
        entries = groups[key]
        if len(entries) < 2:
            continue
        blocks = {_block_label(candidate) for _, candidate in entries}
        proposals = {_proposal_text(candidate) for _, candidate in entries}
        if len(blocks) < 2 or len(proposals) < 2:
            continue

        ordered = sorted(entries, key=lambda pair: (_block_label(pair[1]), candidate_id(pair[1])))
        proposal_records: list[dict[str, Any]] = []
        for item, candidate in ordered:
            evidence = item.get("evidence")
            proposal_records.append(
                {
                    "candidate": candidate_id(candidate),
                    "block": _block_label(candidate),
                    "proposal": _proposal_text(candidate),
                    "message": candidate.get("message") or "",
                    "evidence": evidence if isinstance(evidence, list) else [],
                }
            )
        ids = [record["candidate"] for record in proposal_records]
        seed = json.dumps(
            {"key": key, "candidates": sorted(ids)},
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        first_candidate = ordered[0][1]
        conflicts.append(
            {
                "id": "conflict-" + hashlib.sha256(seed).hexdigest()[:12],
                "key": key,
                "chapter": first_candidate.get("chapter"),
                "segment_index": first_candidate.get("segment_index"),
                "candidates": ids,
                "blocks": sorted(blocks),
                "proposals": proposal_records,
            }
        )
    return conflicts


def build_arbitration_prompt(conflict: dict[str, Any]) -> str:
    """Build one conflict arbitration request."""
    lines = [
        "TASK: ARBITRATE_REVIEW_CONFLICT",
        f"Conflict: {conflict.get('id')} ({conflict.get('key')})",
        "Choose one candidate proposal, or leave the conflict unresolved for a human decision.",
        "Candidate proposals:",
    ]
    for proposal in conflict.get("proposals") or []:
        lines.append(f"[{proposal['candidate']}] block={proposal['block']}")
        lines.append(f"PROPOSAL: {proposal['proposal']}")
        lines.append(f"MESSAGE: {proposal['message']}")
        lines.append("EVIDENCE:")
        evidence = proposal.get("evidence") or []
        if not evidence:
            lines.append("(no evidence references)")
        for ref in evidence:
            lines.append(
                f"[{ref.get('ref')}] SOURCE: {ref.get('source') or ''} "
                f"TARGET: {ref.get('target') or ''}"
            )
        lines.append("")
    lines.extend(
        [
            "Return a JSON object with keys: decision, reason.",
            'decision: one candidate id from above, or "unresolved".',
            "reason: a non-empty string.",
        ]
    )
    return "\n".join(lines)


def _validate_arbitration_output(text: Any) -> dict[str, Any]:
    payload = _parse_json(text)
    if not isinstance(payload, dict):
        raise EvidenceProtocolError("model output must be a JSON object")
    decision = payload.get("decision")
    if not isinstance(decision, str) or not decision.strip():
        raise EvidenceProtocolError("decision must be a non-empty string")
    reason = payload.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        raise EvidenceProtocolError("reason must be a non-empty string")
    return {"decision": decision.strip(), "reason": reason.strip()}


def _arbitration_result(
    conflict: dict[str, Any],
    *,
    resolved: bool,
    winner: str | None,
    reason: str,
    error: str | None,
    ids: list[str],
) -> dict[str, Any]:
    return {
        "conflict": conflict.get("id"),
        "key": conflict.get("key"),
        "resolved": resolved,
        "winner": winner,
        "reason": reason,
        "needs_human": not resolved,
        "error": error,
        "candidates": ids,
    }


def arbitrate_conflict(client: Any, conflict: dict[str, Any]) -> dict[str, Any]:
    """Arbitrate one conflict, leaving it flagged when the model cannot decide."""
    ids = list(conflict.get("candidates") or [])
    if not ids:
        ids = [
            str(proposal.get("candidate"))
            for proposal in conflict.get("proposals") or []
            if proposal.get("candidate")
        ]
    if not ids:
        raise ValueError("conflict must contain at least one candidate")

    prompt = build_arbitration_prompt(conflict)
    try:
        payload = _validate_arbitration_output(call_client(client, prompt))
    except Exception as exc:
        return _arbitration_result(
            conflict,
            resolved=False,
            winner=None,
            reason="",
            error=str(exc) or exc.__class__.__name__,
            ids=ids,
        )

    decision = payload["decision"]
    if decision == "unresolved":
        return _arbitration_result(
            conflict,
            resolved=False,
            winner=None,
            reason=payload["reason"],
            error=None,
            ids=ids,
        )
    if decision not in ids:
        return _arbitration_result(
            conflict,
            resolved=False,
            winner=None,
            reason=payload["reason"],
            error=f"decision {decision!r} is not one of the conflict candidates",
            ids=ids,
        )
    return _arbitration_result(
        conflict,
        resolved=True,
        winner=decision,
        reason=payload["reason"],
        error=None,
        ids=ids,
    )


def arbitrate(client: Any, conflicts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Arbitrate conflicts in their original order."""
    return [arbitrate_conflict(client, conflict) for conflict in conflicts]


def review_with_arbitration(
    client: Any,
    reviews: list[dict[str, Any]],
    blocks: list[list[dict[str, Any]] | dict[str, Any]],
    chapters: list[dict[str, Any]],
    *,
    max_rounds: int = DEFAULT_EVIDENCE_ROUNDS,
    max_refs: int = DEFAULT_MAX_EVIDENCE_REFS,
    max_chars: int = DEFAULT_MAX_EVIDENCE_CHARS,
    verified_only: bool = False,
) -> dict[str, Any]:
    """Verify review candidates, group conflicts and arbitrate them.

    By default conflicts include rejected and unresolved candidates, so a
    contradiction is not silently dropped just because evidence did not confirm it.
    The result keeps candidates in review order, arbitration results in conflict
    order, and exposes unresolved conflicts separately for human review.
    """
    candidates = collect_candidates(reviews, blocks)
    verified = verify_candidates(
        client,
        candidates,
        chapters,
        max_rounds=max_rounds,
        max_refs=max_refs,
        max_chars=max_chars,
    )
    conflicts = group_conflicts(verified, verified_only=verified_only)
    arbitrations = arbitrate(client, conflicts)
    return {
        "candidates": verified,
        "conflicts": arbitrations,
        "unresolved_conflicts": [result for result in arbitrations if result["needs_human"]],
        "confirmed": [record for record in verified if record["status"] == "confirmed"],
        "rejected": [record for record in verified if record["status"] == "rejected"],
        "unresolved_candidates": [
            record for record in verified if record["status"] == "unresolved"
        ],
    }
