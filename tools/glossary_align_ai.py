#!/usr/bin/env python3
"""AI-assisted glossary alignment before whole-book translation.

Usage:
    .venv/bin/python tools/glossary_align_ai.py works/<series>/books/book-01
    .venv/bin/python tools/glossary_align_ai.py works/<series>/books/book-01 --dry-run

The deterministic layer finds source proper nouns that the merged glossary does
not cover.  A low-effort model then classifies each candidate as an alias of an
existing term, a new series term, a deliberate coverage exception, or unresolved.
The model only proposes structured decisions; this module validates them and
writes only the series master and book copies.  The global glossary is never
modified.  Unresolved or invalid decisions raise ``GlossaryAlignmentError`` and
leave the glossary untouched.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any

import align_glossary
import book_state
import segment_state
from model_client import call_client

BATCH_SIZE = 20
DEFAULT_MAX_TOKENS = 8192
CANDIDATE_CONTEXT_LIMIT = 3
CANDIDATE_CHARS = 360


class GlossaryAlignmentError(RuntimeError):
    """Raised when AI decisions cannot be validated or coverage remains."""


SYSTEM = (
    "你是术语表对齐助手。你只输出 JSON，不写解释。"
    "你的任务是判断英文专名是已有术语的别名、需要新建的系列术语、"
    "普通词/标题碎片，还是无法判断。"
)

INSTRUCTIONS = """Return JSON only:
{"decisions":[
 {"candidate":"...", "action":"alias|new|ignore|unresolved",
  "term_id":"existing term id (alias only)",
  "source":"...", "target":"...", "aliases":["..."],
  "canonical_forms":["..."], "category":"person|place|object|creature|concept|term|spell|plant",
  "reason":"short reason"}
]}

Rules:
- Use alias when the candidate is a partial name or alias of an existing term.
  term_id must be an existing id.
- Use new only for a genuinely new series term. target must be Chinese and must
  not contain English letters. canonical_forms may include accepted abbreviations.
- Use ignore only for common words or title fragments that are not proper names.
- Use unresolved if the context is insufficient or two terms conflict.
- Every candidate must have exactly one decision.
"""


def _is_series_book(book: Path) -> bool:
    return book_state.is_series_root(book.parent.parent)


def _candidate_pattern(candidate: str) -> re.Pattern[str]:
    return re.compile(r"(?<![A-Za-z0-9])" + re.escape(candidate) + r"(?![A-Za-z0-9])", re.IGNORECASE)


def _contexts(book: Path, candidate: str) -> list[dict[str, Any]]:
    pattern = _candidate_pattern(candidate)
    found: list[dict[str, Any]] = []
    for path in sorted((book / "source" / segment_state.CHAPTERS_DIR).glob("ch-*.json")):
        chapter = segment_state.load_chapter(path)
        for segment in chapter.get("segments") or []:
            source = segment.get("source") or ""
            if not pattern.search(source):
                continue
            found.append(
                {
                    "chapter": chapter.get("chapter"),
                    "index": segment.get("index"),
                    "source": source[:CANDIDATE_CHARS],
                    "target": (segment.get("target") or "")[:CANDIDATE_CHARS],
                }
            )
            if len(found) >= CANDIDATE_CONTEXT_LIMIT:
                return found
    return found


def _related_terms(terms: list[dict[str, Any]], candidate: str) -> list[dict[str, Any]]:
    needle = candidate.casefold()
    related: list[dict[str, Any]] = []
    for term in terms:
        forms = [str(term.get("source") or ""), *[str(x) for x in term.get("aliases") or []]]
        forms = [form for form in forms if form]
        if any(
            needle in form.casefold() or form.casefold() in needle for form in forms
        ):
            related.append(
                {
                    "id": term.get("id"),
                    "source": term.get("source"),
                    "target": term.get("target"),
                    "aliases": term.get("aliases") or [],
                    "canonical_forms": term.get("canonical_forms") or [],
                }
            )
            if len(related) >= 12:
                break
    return related


def _prompt(
    candidates: list[str], terms: list[dict[str, Any]], book: Path
) -> str:
    payload = []
    for candidate in candidates:
        payload.append(
            {
                "candidate": candidate,
                "contexts": _contexts(book, candidate),
                "related_terms": _related_terms(terms, candidate),
            }
        )
    return (
        INSTRUCTIONS
        + "\nCandidates:\n"
        + json.dumps(payload, ensure_ascii=False)
        + "\n"
    )


def _parse_json(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise GlossaryAlignmentError("model output is not a JSON object")
    try:
        data = json.loads(text[start : end + 1])
    except ValueError as exc:
        raise GlossaryAlignmentError("model output is not valid JSON") from exc
    if not isinstance(data, dict):
        raise GlossaryAlignmentError("model output must be a JSON object")
    return data


def _decisions(
    client: Any, book: Path, terms: list[dict[str, Any]], candidates: list[str]
) -> list[dict[str, Any]]:
    decisions: list[dict[str, Any]] = []
    for offset in range(0, len(candidates), BATCH_SIZE):
        batch = candidates[offset : offset + BATCH_SIZE]
        data = _parse_json(call_client(client, _prompt(batch, terms, book)))
        items = data.get("decisions")
        if not isinstance(items, list):
            raise GlossaryAlignmentError("model output must contain a decisions array")
        decisions.extend(item for item in items if isinstance(item, dict))
    return decisions


def _validate(
    book: Path,
    candidates: list[str],
    decisions: list[dict[str, Any]],
    terms: list[dict[str, Any]],
    ignores: set[str],
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    by_id = {str(term.get("id") or ""): term for term in terms}
    expected = {candidate.casefold(): candidate for candidate in candidates}
    seen: dict[str, dict[str, Any]] = {}
    for decision in decisions:
        candidate = str(decision.get("candidate") or "").strip()
        key = candidate.casefold()
        if key not in expected:
            raise GlossaryAlignmentError(f"unknown candidate in model output: {candidate!r}")
        if key in seen:
            raise GlossaryAlignmentError(f"duplicate decision for candidate: {candidate!r}")
        seen[key] = decision

    unresolved = [candidate for key, candidate in expected.items() if key not in seen]
    aliases: list[dict[str, Any]] = []
    new_terms: list[dict[str, Any]] = []
    new_ignores: list[str] = []
    covered = align_glossary._covered_sources(terms) | ignores
    for key, candidate in expected.items():
        decision = seen[key]
        action = str(decision.get("action") or "").strip()
        if action == "alias":
            term_id = str(decision.get("term_id") or "").strip()
            if term_id not in by_id:
                raise GlossaryAlignmentError(
                    f"alias decision names unknown term id: {term_id!r}"
                )
            aliases.append({"term_id": term_id, "candidate": candidate})
            covered.add(candidate.casefold())
        elif action == "new":
            source = str(decision.get("source") or candidate).strip()
            target = str(decision.get("target") or "").strip()
            if not source:
                raise GlossaryAlignmentError(f"new term has no source: {candidate!r}")
            if not target or not re.search(r"[\u4e00-\u9fff]", target):
                raise GlossaryAlignmentError(
                    f"new term target must be Chinese: {candidate!r}"
                )
            if re.search(r"[A-Za-z]", target):
                raise GlossaryAlignmentError(
                    f"new term target must not contain English: {candidate!r}"
                )
            if source in by_id:
                raise GlossaryAlignmentError(
                    f"new term id already exists; use alias: {source!r}"
                )
            canonical = [
                str(value).strip()
                for value in decision.get("canonical_forms") or []
                if str(value).strip()
            ]
            if target not in canonical:
                canonical.insert(0, target)
            aliases_out = [
                str(value).strip()
                for value in decision.get("aliases") or []
                if str(value).strip()
            ]
            term = {
                "id": source,
                "source": source,
                "target": target,
                "aliases": aliases_out,
                "canonical_forms": canonical,
                "category": str(decision.get("category") or "term"),
                "confidence": "high",
                "evidence_refs": [],
                "frequency": 0,
                "notes": str(decision.get("reason") or ""),
            }
            new_terms.append(term)
            for value in [term["source"], *term["aliases"]]:
                covered.add(value.casefold())
                for word in value.split():
                    covered.add(word.casefold())
        elif action == "ignore":
            if candidate.casefold() not in ignores:
                new_ignores.append(candidate)
            covered.add(candidate.casefold())
        elif action == "unresolved":
            unresolved.append(candidate)
        else:
            raise GlossaryAlignmentError(
                f"unknown action for {candidate!r}: {action!r}"
            )
    uncovered = [
        candidate for candidate in expected.values() if candidate.casefold() not in covered
    ]
    unresolved = sorted(set(unresolved + uncovered))
    return aliases, new_terms, new_ignores, unresolved


def _apply(
    book: Path,
    aliases: list[dict[str, Any]],
    new_terms: list[dict[str, Any]],
    ignores: list[str],
    dry_run: bool,
) -> dict[str, Any]:
    series = book.parent.parent
    master_path = series / book_state.SERIES_GLOSSARY
    if not master_path.is_file():
        raise GlossaryAlignmentError(f"series glossary not found: {master_path}")
    data = json.loads(master_path.read_text(encoding="utf-8"))
    terms = list(data.get("terms") or [])
    by_id = {str(term.get("id") or ""): term for term in terms}
    for item in aliases:
        term = by_id[item["term_id"]]
        names = list(term.get("aliases") or [])
        if item["candidate"] not in names and item["candidate"] != term.get("source"):
            names.append(item["candidate"])
        if names:
            term["aliases"] = names
    for term in new_terms:
        if term["id"] in by_id:
            raise GlossaryAlignmentError(f"duplicate term id: {term['id']!r}")
        terms.append(term)
        by_id[term["id"]] = term
    existing_ignores = list(data.get("coverage_ignore") or [])
    for candidate in ignores:
        if candidate not in existing_ignores:
            existing_ignores.append(candidate)
    if existing_ignores:
        data["coverage_ignore"] = existing_ignores
    data["terms"] = terms
    if dry_run:
        return {"applied": False, "aliases": aliases, "new_terms": new_terms, "ignores": ignores}
    master_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    synced = []
    for path in sorted((series / book_state.BOOKS_DIR).glob("book-*")):
        if (path / "meta.json").is_file():
            book_state.sync_glossary(path)
            synced.append(str(path))
    return {
        "applied": True,
        "aliases": aliases,
        "new_terms": new_terms,
        "ignores": ignores,
        "synced_books": synced,
    }


def align_book(
    book: Path,
    client: Any,
    *,
    min_count: int = 3,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Align missing source proper nouns through a low-effort model call.

    The function returns a report.  It raises ``GlossaryAlignmentError`` and
    writes nothing when any decision is invalid, unresolved, or coverage remains.
    """
    book = Path(book)
    if not _is_series_book(book):
        raise GlossaryAlignmentError(f"not a series book: {book}")
    terms = book_state.load_glossary_terms(book)
    coverage = align_glossary.coverage_report(book, terms, min_count=min_count)
    candidates = [item["candidate"] for item in coverage["missing"]]
    if not candidates:
        return {"applied": False, "candidates": 0, "coverage_missing": 0}
    decisions = _decisions(client, book, terms, candidates)
    series = book.parent.parent
    master = json.loads((series / book_state.SERIES_GLOSSARY).read_text(encoding="utf-8"))
    ignores = {str(value).casefold() for value in master.get("coverage_ignore") or []}
    aliases, new_terms, new_ignores, unresolved = _validate(
        book, candidates, decisions, terms, ignores
    )
    if unresolved:
        raise GlossaryAlignmentError(
            "unresolved glossary candidates: " + ", ".join(sorted(set(unresolved)))
        )
    result = _apply(book, aliases, new_terms, new_ignores, dry_run)
    if dry_run:
        result["candidates"] = len(candidates)
        result["coverage_missing"] = len(candidates)
        result["decisions"] = decisions
        return result
    terms = book_state.load_glossary_terms(book)
    remaining = align_glossary.coverage_report(book, terms, min_count=min_count)["missing"]
    if remaining:
        raise GlossaryAlignmentError(
            "coverage still missing after alignment: "
            + ", ".join(item["candidate"] for item in remaining)
        )
    result["candidates"] = len(candidates)
    result["coverage_missing"] = len(remaining)
    result["decisions"] = decisions
    return result


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("book_dir", type=Path)
    parser.add_argument(
        "--model",
        default=os.environ.get("MALFOID_MODEL", "DeepSeek-V4.1-Flash-1017-TensorFold-Zig"),
    )
    parser.add_argument("--effort", default="low")
    parser.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS)
    parser.add_argument("--min-count", type=int, default=3)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if not args.book_dir.is_dir():
        raise SystemExit(f"book dir not found: {args.book_dir}")
    from model_api import make_client

    client = make_client(
        args.model,
        args.effort,
        args.max_tokens,
        usage_log=args.book_dir / "work_temp" / "glossary_alignment_usage.jsonl",
        system=SYSTEM,
    )
    result = align_book(
        args.book_dir, client, min_count=args.min_count, dry_run=args.dry_run
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
