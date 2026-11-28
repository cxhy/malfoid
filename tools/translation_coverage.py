#!/usr/bin/env python3
"""Deterministic source-to-target coverage checks for a translated v2 book.

This module looks for segments where the target is much shorter than the
chapter's normal source/target ratio, where sentence density collapses, or where
a source scene separator near the end of a segment has no counterpart in the
target. It is a suspect finder, not a semantic judge: warnings are candidates
for human or model review, and only extreme length/sentence outliers are errors.

Usage from ``verify_book``:
    report = translation_coverage.coverage_report(book_dir)
"""

from __future__ import annotations

import re
import statistics
from pathlib import Path
from typing import Any

import segment_state

_SOURCE_SENTENCE_RE = re.compile(r"""[.!?]+["'”’)\]]*(?=\s|$)""")
_TARGET_SENTENCE_RE = re.compile(r"[。！？…]+[”’）]*")
_SEPARATOR_RE = re.compile(r"(?m)^---\s*$")

SHORT_ERROR_RATIO = 0.5
SHORT_WARNING_RATIO = 0.7
LONG_WARNING_RATIO = 1.8
SENTENCE_ERROR_RATIO = 0.35
SENTENCE_WARNING_RATIO = 0.5
SEPARATOR_TAIL_MIN_CHARS = 80
SEPARATOR_TAIL_MIN_POSITION = 0.5
SEPARATOR_TAIL_MIN_SENTENCE_RATIO = 0.9


def _source_sentences(text: str) -> int:
    return len(_SOURCE_SENTENCE_RE.findall(text))


def _target_sentences(text: str) -> int:
    return len(_TARGET_SENTENCE_RE.findall(text))


def _separators(text: str) -> list[int]:
    return [match.start() for match in _SEPARATOR_RE.finditer(text)]


def _tail_after_last_separator(text: str, positions: list[int]) -> str:
    if not positions:
        return ""
    return text[positions[-1] + 3:].strip()


def _chapter_paths(book_dir: Path) -> list[Path]:
    def sort_key(path: Path) -> float:
        match = re.search(r"(\d+(?:\.\d+)?)$", path.stem)
        return float(match.group(1)) if match else 0.0

    return sorted(
        (Path(book_dir) / "source" / segment_state.CHAPTERS_DIR).glob("ch-*.json"),
        key=sort_key,
    )


def _issue(
    severity: str,
    kind: str,
    chapter: int | float,
    segment: dict[str, Any],
    **details: Any,
) -> dict[str, Any]:
    return {
        "severity": severity,
        "kind": kind,
        "chapter": chapter,
        "index": segment.get("index"),
        **details,
    }


def coverage_report(book_dir: Path) -> dict[str, Any]:
    """Return coverage errors, warnings and per-chapter segment statistics.

    The ratio baseline is the median source/target character ratio of the
    chapter. A chapter with too few translated text segments cannot establish a
    stable baseline, so ratio and sentence checks are skipped there; separator
    checks still run.
    """
    book = Path(book_dir)
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    chapters_report: list[dict[str, Any]] = []
    segment_count = 0

    for path in _chapter_paths(book):
        chapter = segment_state.load_chapter(path)
        chapter_number = chapter.get("chapter")
        candidates = [
            segment
            for segment in chapter.get("segments") or []
            if segment.get("kind") == "text"
            and (segment.get("source") or "").strip()
            and (segment.get("target") or "").strip()
        ]
        ratios = []
        sentence_ratios = []
        for segment in candidates:
            source = segment.get("source") or ""
            target = segment.get("target") or ""
            if source:
                ratios.append(len(target) / len(source))
            source_sentences = _source_sentences(source)
            if source_sentences:
                sentence_ratios.append(_target_sentences(target) / source_sentences)
        median_ratio = statistics.median(ratios) if len(ratios) >= 2 else None
        median_sentence_ratio = (
            statistics.median(sentence_ratios) if len(sentence_ratios) >= 2 else None
        )

        chapter_issues = []
        for segment in candidates:
            segment_count += 1
            source = segment.get("source") or ""
            target = segment.get("target") or ""
            source_chars = len(source)
            target_chars = len(target)
            ratio = target_chars / source_chars if source_chars else None
            source_sentences = _source_sentences(source)
            target_sentences = _target_sentences(target)
            sentence_ratio = (
                target_sentences / source_sentences if source_sentences else None
            )
            separators = _separators(source)
            target_separators = len(_separators(target))
            tail = _tail_after_last_separator(source, separators)
            tail_position = (
                separators[-1] / source_chars if separators and source_chars else 0
            )

            issues: list[dict[str, Any]] = []
            if median_ratio is not None and ratio is not None:
                if ratio < median_ratio * SHORT_ERROR_RATIO:
                    issues.append(
                        _issue(
                            "error",
                            "target_too_short",
                            chapter_number,
                            segment,
                            ratio=round(ratio, 3),
                            median=round(median_ratio, 3),
                            source_chars=source_chars,
                            target_chars=target_chars,
                        )
                    )
                elif ratio < median_ratio * SHORT_WARNING_RATIO:
                    issues.append(
                        _issue(
                            "warning",
                            "target_short",
                            chapter_number,
                            segment,
                            ratio=round(ratio, 3),
                            median=round(median_ratio, 3),
                            source_chars=source_chars,
                            target_chars=target_chars,
                        )
                    )
                elif ratio > median_ratio * LONG_WARNING_RATIO:
                    issues.append(
                        _issue(
                            "warning",
                            "target_long",
                            chapter_number,
                            segment,
                            ratio=round(ratio, 3),
                            median=round(median_ratio, 3),
                            source_chars=source_chars,
                            target_chars=target_chars,
                        )
                    )
            if (
                median_sentence_ratio is not None
                and sentence_ratio is not None
                and source_sentences >= 5
            ):
                if sentence_ratio < median_sentence_ratio * SENTENCE_ERROR_RATIO:
                    issues.append(
                        _issue(
                            "error",
                            "target_too_few_sentences",
                            chapter_number,
                            segment,
                            sentence_ratio=round(sentence_ratio, 3),
                            median=round(median_sentence_ratio, 3),
                            source_sentences=source_sentences,
                            target_sentences=target_sentences,
                        )
                    )
                elif sentence_ratio < median_sentence_ratio * SENTENCE_WARNING_RATIO:
                    issues.append(
                        _issue(
                            "warning",
                            "target_fewer_sentences",
                            chapter_number,
                            segment,
                            sentence_ratio=round(sentence_ratio, 3),
                            median=round(median_sentence_ratio, 3),
                            source_sentences=source_sentences,
                            target_sentences=target_sentences,
                        )
                    )
            if (
                separators
                and target_separators < len(separators)
                and len(tail) >= SEPARATOR_TAIL_MIN_CHARS
                and tail_position >= SEPARATOR_TAIL_MIN_POSITION
            ):
                target_positions = _separators(target)
                mapped_index = (
                    min(len(target_positions) - 1, len(separators) - 1)
                    if target_positions
                    else -1
                )
                mapped_source_tail = (
                    source[separators[mapped_index] + 3:] if mapped_index >= 0 else source
                )
                target_tail = (
                    target[target_positions[-1] + 3:] if target_positions else target
                )
                source_tail_sentences = _source_sentences(mapped_source_tail)
                target_tail_sentences = _target_sentences(target_tail)
                tail_sentence_ratio = (
                    target_tail_sentences / source_tail_sentences
                    if source_tail_sentences
                    else None
                )
                if (
                    tail_sentence_ratio is None
                    or tail_sentence_ratio < SEPARATOR_TAIL_MIN_SENTENCE_RATIO
                ):
                    issues.append(
                        _issue(
                            "warning",
                            "possible_missing_scene_tail",
                            chapter_number,
                            segment,
                            source_separators=len(separators),
                            target_separators=target_separators,
                            source_tail_chars=len(tail),
                            tail_position=round(tail_position, 3),
                            tail_sentence_ratio=(
                                round(tail_sentence_ratio, 3)
                                if tail_sentence_ratio is not None
                                else None
                            ),
                        )
                    )
            if target_separators > len(separators):
                issues.append(
                    _issue(
                        "warning",
                        "extra_scene_separator",
                        chapter_number,
                        segment,
                        source_separators=len(separators),
                        target_separators=target_separators,
                    )
                )

            for issue in issues:
                if issue["severity"] == "error":
                    errors.append(issue)
                else:
                    warnings.append(issue)
            chapter_issues.extend(issues)

        chapters_report.append(
            {
                "chapter": chapter_number,
                "segments": len(candidates),
                "median_ratio": round(median_ratio, 3) if median_ratio is not None else None,
                "median_sentence_ratio": (
                    round(median_sentence_ratio, 3)
                    if median_sentence_ratio is not None
                    else None
                ),
                "issues": chapter_issues,
            }
        )

    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "chapters": chapters_report,
        "counts": {
            "segments": segment_count,
            "errors": len(errors),
            "warnings": len(warnings),
            "suspects": len(errors) + len(warnings),
        },
    }
