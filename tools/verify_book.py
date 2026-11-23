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

import align_glossary
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


def verify_book(
    book_dir: Path,
    *,
    strict_coverage: bool = False,
    min_coverage_count: int = 2,
) -> dict[str, Any]:
    """Run deterministic checks and return a machine-readable report.

    ``strict_coverage`` turns uncovered source proper nouns into errors. The
    default is false so legacy works and test fixtures without a glossary still
    pass; delivery enables it for series books.
    """
    book = Path(book_dir)
    chapters = _chapters(book)
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    blob = _segment_text(chapters)
    glossary = book_state.load_glossary_terms(book)
    alignment = align_glossary.scan_alignment(book, glossary, chapters)
    coverage = align_glossary.coverage_report(
        book, glossary, chapters, min_count=min_coverage_count
    )

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

    errors.extend(alignment["errors"])
    warnings.extend(alignment["warnings"])
    if strict_coverage:
        errors.extend(
            {"kind": "glossary_coverage", **item} for item in coverage["missing"]
        )

    return {
        "book_dir": str(book),
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "coverage": coverage,
        "alignment": {
            "errors": alignment["errors"],
            "warnings": alignment["warnings"],
            "counts": alignment["counts"],
        },
        "counts": {
            "chapters": len(chapters),
            "headings": heading_count,
            "missing": len(missing),
            "english_words": len(english),
            "variant_errors": len(alignment["errors"]),
            "canonical_missing": len(alignment["warnings"]),
            "coverage_missing": len(coverage["missing"]),
        },
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("book_dir", type=Path)
    parser.add_argument("--json", action="store_true", help="print the full report")
    parser.add_argument(
        "--strict-coverage",
        action="store_true",
        help="treat uncovered source proper nouns as errors",
    )
    parser.add_argument(
        "--coverage-min-count",
        type=int,
        default=3,
        help="minimum source occurrences for a coverage error (default: 3)",
    )
    args = parser.parse_args(argv)
    report = verify_book(
        args.book_dir,
        strict_coverage=args.strict_coverage,
        min_coverage_count=args.coverage_min_count,
    )
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
