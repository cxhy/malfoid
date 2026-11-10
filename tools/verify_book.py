#!/usr/bin/env python3
"""Deterministic delivery checks for a translated v2 book.

Usage:
    .venv/bin/python tools/verify_book.py works/<series>/books/book-01

Checks completeness, forbidden names, English residue, glossary variant drift and
heading format. Errors block delivery; warnings are reported but do not. The tool
is deterministic and makes no model calls, so it can run on every delivery.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

import book_state
import profile_rules
import segment_state

ENGLISH_RESIDUE = re.compile(r"[A-Za-z]{3,}")
BARE_DRACO = re.compile(r"(?<!妮娅)德拉科(?!妮娅)")
HEADING_DIGIT = re.compile(r"[0-9]")


class DeliveryVerificationError(RuntimeError):
    """Raised when a book fails a blocking delivery check."""

    def __init__(self, report: dict[str, Any]) -> None:
        self.report = report
        super().__init__(f"delivery verification failed: {len(report.get('errors') or [])} error(s)")


def _chapters(book_dir: Path) -> list[dict[str, Any]]:
    chapters_dir = Path(book_dir) / "source" / segment_state.CHAPTERS_DIR

    def sort_key(path: Path) -> float:
        match = re.search(r"(\d+(?:\.\d+)?)$", path.stem)
        return float(match.group(1)) if match else 0.0

    return [
        segment_state.load_chapter(path)
        for path in sorted(chapters_dir.glob("ch-*.json"), key=sort_key)
    ]


def _segment_text(chapters: list[dict[str, Any]]) -> str:
    return "\n".join(
        (segment.get("target") or "")
        for chapter in chapters
        for segment in chapter.get("segments") or []
    )


def _glossary_variants(book_dir: Path) -> list[tuple[str, str]]:
    """Return (term id, forbidden target) pairs from the merged glossary."""
    variants: list[tuple[str, str]] = []
    for term in book_state.load_glossary_terms(book_dir):
        term_id = str(term.get("id") or term.get("source") or "")
        canonical = str(term.get("target") or "")
        for variant in term.get("forbidden_targets") or []:
            if not isinstance(variant, str) or not variant.strip():
                continue
            if canonical and variant in canonical:
                continue
            variants.append((term_id, variant))
    return variants


def verify_book(book_dir: Path) -> dict[str, Any]:
    """Run deterministic checks and return a machine-readable report."""
    book = Path(book_dir)
    chapters = _chapters(book)
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    blob = _segment_text(chapters)

    missing: list[dict[str, Any]] = []
    heading_count = 0
    for chapter in chapters:
        for segment in chapter.get("segments") or []:
            source = (segment.get("source") or "").strip()
            target = (segment.get("target") or "").strip()
            if segment.get("kind") == "heading":
                heading_count += 1
                display = segment_state.display_chapter_heading(chapter)
                heading_target = display or target
                if heading_target:
                    if HEADING_DIGIT.search(heading_target):
                        warnings.append(
                            {
                                "kind": "heading_digits",
                                "chapter": chapter.get("chapter"),
                                "index": segment.get("index"),
                                "target": heading_target,
                            }
                        )
                    if not (
                        heading_target.startswith("第") and heading_target.endswith("章")
                    ):
                        warnings.append(
                            {
                                "kind": "heading_shape",
                                "chapter": chapter.get("chapter"),
                                "index": segment.get("index"),
                                "target": heading_target,
                            }
                        )
                    if "(" in heading_target or "（" in heading_target:
                        warnings.append(
                            {
                                "kind": "heading_parenthetical",
                                "chapter": chapter.get("chapter"),
                                "index": segment.get("index"),
                                "target": heading_target,
                            }
                        )
            if source and not target:
                missing.append(
                    {
                        "chapter": chapter.get("chapter"),
                        "index": segment.get("index"),
                    }
                )
    if missing:
        errors.append({"kind": "missing_targets", "count": len(missing), "items": missing[:20]})

    forbidden = profile_rules.forbidden_names(work_dir=book)
    for name in forbidden:
        if name and name in blob:
            errors.append({"kind": "forbidden_name", "name": name, "count": blob.count(name)})
    if profile_rules.female_draco(work_dir=book) and BARE_DRACO.search(blob):
        matches = BARE_DRACO.findall(blob)
        errors.append(
            {"kind": "bare_draco", "name": "德拉科", "count": len(matches)}
        )

    english_blob = blob
    for phrase in profile_rules.foreign_text_allowlist(work_dir=book):
        english_blob = english_blob.replace(phrase, "")
    english = sorted(set(ENGLISH_RESIDUE.findall(english_blob)))
    if english:
        errors.append({"kind": "english_residue", "words": english[:20]})

    for term_id, variant in _glossary_variants(book):
        count = blob.count(variant)
        if count:
            errors.append(
                {
                    "kind": "glossary_variant",
                    "term": term_id,
                    "variant": variant,
                    "count": count,
                }
            )

    return {
        "book_dir": str(book),
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "counts": {
            "chapters": len(chapters),
            "headings": heading_count,
            "missing": len(missing),
            "english_words": len(english),
        },
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("book_dir", type=Path)
    parser.add_argument("--json", action="store_true", help="print the full report")
    args = parser.parse_args(argv)
    report = verify_book(args.book_dir)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(
            json.dumps(
                {
                    "ok": report["ok"],
                    "errors": len(report["errors"]),
                    "warnings": len(report["warnings"]),
                    "counts": report["counts"],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    if not report["ok"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
