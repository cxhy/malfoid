#!/usr/bin/env python3
"""Translate a whole v2 book from chapter/segment state.

Usage:
    .venv/bin/python tools/translate_book_v2.py works/<series>/books/book-01
    .venv/bin/python tools/translate_book_v2.py works/<series>/books/book-01 --no-polish

The book must already have chapter state under ``source/chapters``. Pending
segments are translated through the equal-length batch protocol, then polished
through the same contract. Existing targets are left alone, so reruns continue
where the previous run stopped. Review and delivery are separate commands.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any

import batch_translate
import book_state
import polish_batch
import segment_state
from profile_rules import section, section_with_skill


class TimeBudgetExceeded(RuntimeError):
    """Raised between saved batches when a translate run reaches its time budget."""


def load_glossary_terms(book_dir: Path) -> list[dict[str, Any]]:
    """Load the merged series + book glossary for translation."""
    return book_state.load_glossary_terms(book_dir)


def _chapter_paths(
    book_dir: Path, chapters: list[int | float] | None = None
) -> list[Path]:
    chapters_dir = book_dir / "source" / segment_state.CHAPTERS_DIR

    def number_of(path: Path) -> int | float:
        return segment_state.chapter_number(path.stem.split("-")[-1])

    paths = sorted(chapters_dir.glob("ch-*.json"), key=number_of)
    if chapters is not None:
        wanted = {segment_state.chapter_number(value) for value in chapters}
        paths = [path for path in paths if number_of(path) in wanted]
    return paths


def _pending(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        segment
        for segment in segments
        if (segment.get("source") or "").strip() and not (segment.get("target") or "").strip()
    ]


def _append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _chapter_tail(chapter: dict[str, Any]) -> str:
    texts = [
        block["text"]
        for block in segment_state.logical_blocks(chapter)
        if block["kind"] == "text" and block["text"].strip()
    ]
    return "\n\n".join(texts[-2:])[:800]


def translate_book(
    book_dir: Path,
    client: Any,
    *,
    budget: int = batch_translate.DEFAULT_BATCH_BUDGET,
    max_retries: int = batch_translate.MAX_PROTOCOL_RETRIES,
    polish: bool = True,
    chapters: list[int | float] | None = None,
    progress_path: Path | None = None,
    max_seconds: float | None = None,
) -> dict[str, Any]:
    """Translate pending segments and optionally polish them; return a summary.

    Progress is saved after every batch, so an interrupted run keeps all completed
    batches. ``max_seconds`` stops between batches and returns ``stopped=True``;
    rerunning the same command continues from the saved state.
    """
    terms = load_glossary_terms(book_dir)
    translation_rules = section("translation", work_dir=book_dir)
    polish_rules = section_with_skill("polish", mode="轻改", work_dir=book_dir)
    translated = polished = 0
    failures: list[dict[str, Any]] = []
    context = ""
    stopped = False
    started = time.time()
    progress_path = progress_path or book_dir / "work_temp" / "progress.jsonl"
    for path in _chapter_paths(book_dir, chapters):
        chapter_started = time.time()
        chapter = segment_state.load_chapter(path)
        pending = _pending(chapter.get("segments") or [])
        by_index = {int(segment.get("index", 0)): segment for segment in chapter["segments"]}
        chapter_translated = chapter_polished = chapter_failures = 0
        if pending:

            def handle_batch(batch_results: list[dict[str, Any]]) -> None:
                nonlocal translated, polished, chapter_translated, chapter_polished, chapter_failures
                stats = batch_translate.apply_targets(chapter, batch_results)
                translated += stats["applied"]
                chapter_translated += stats["applied"]
                batch_errors = [result for result in batch_results if result.get("error")]
                chapter_failures += len(batch_errors)
                failures.extend(
                    {"chapter": chapter.get("chapter"), **result} for result in batch_errors
                )
                if polish:
                    to_polish = []
                    for result in batch_results:
                        segment = by_index.get(int(result.get("index", -1)))
                        if segment is not None and (segment.get("target") or "").strip():
                            to_polish.append(segment)
                    if to_polish:
                        polished_results = polish_batch.polish_batch(
                            client,
                            to_polish,
                            budget=budget,
                            max_retries=max_retries,
                            rules=polish_rules,
                            glossary_terms=terms,
                        )
                        polished_stats = polish_batch.apply_polished(chapter, polished_results)
                        polished += polished_stats["applied"]
                        chapter_polished += polished_stats["applied"]
                        polish_errors = [
                            result for result in polished_results if result.get("error")
                        ]
                        chapter_failures += len(polish_errors)
                        failures.extend(
                            {"chapter": chapter.get("chapter"), **result}
                            for result in polish_errors
                        )
                segment_state.save_chapter(path, chapter)
                if max_seconds is not None and time.time() - started >= max_seconds:
                    raise TimeBudgetExceeded()

            try:
                batch_translate.translate_batch(
                    client,
                    pending,
                    budget=budget,
                    max_retries=max_retries,
                    glossary_terms=terms,
                    context=context,
                    rules=translation_rules,
                    on_batch=handle_batch,
                )
            except TimeBudgetExceeded:
                stopped = True
        _append_jsonl(
            progress_path,
            {
                "ts": time.time(),
                "chapter": chapter.get("chapter"),
                "pending": len(pending),
                "translated": chapter_translated,
                "polished": chapter_polished,
                "failures": chapter_failures,
                "elapsed": round(time.time() - chapter_started, 2),
            },
        )
        if stopped:
            break
        context = _chapter_tail(chapter)
    return {
        "book_dir": str(book_dir),
        "translated": translated,
        "polished": polished,
        "failures": failures,
        "polish": polish,
        "stopped": stopped,
        "elapsed": round(time.time() - started, 1),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("book_dir", type=Path)
    ap.add_argument(
        "--model",
        default=os.environ.get("MALFOID_MODEL", "DeepSeek-V4.1-Flash-1017-TensorFold-Zig"),
    )
    ap.add_argument("--effort", default="low")
    ap.add_argument("--max-tokens", type=int, default=32768)
    ap.add_argument("--budget", type=int, default=batch_translate.DEFAULT_BATCH_BUDGET)
    ap.add_argument(
        "--max-seconds",
        type=float,
        default=0,
        help="stop gracefully between batches after N seconds (0 = no limit)",
    )
    ap.add_argument("--no-polish", action="store_true")
    ap.add_argument(
        "--chapters",
        default=None,
        help="comma-separated chapter numbers to translate (default: all)",
    )
    args = ap.parse_args()
    chapters = None
    if args.chapters:
        chapters = [
            segment_state.chapter_number(value.strip())
            for value in args.chapters.split(",")
            if value.strip()
        ]

    if not args.book_dir.is_dir():
        raise SystemExit(f"book dir not found: {args.book_dir}")
    from model_api import make_client

    client = make_client(
        args.model,
        args.effort,
        args.max_tokens,
        usage_log=args.book_dir / "work_temp" / "usage.jsonl",
    )
    summary = translate_book(
        args.book_dir,
        client,
        budget=args.budget,
        polish=not args.no_polish,
        chapters=chapters,
        max_seconds=args.max_seconds or None,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if summary["failures"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
