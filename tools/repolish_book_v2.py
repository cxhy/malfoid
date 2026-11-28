#!/usr/bin/env python3
"""Re-polish existing translations with the configured polish skill.

Usage:
    .venv/bin/python tools/repolish_book_v2.py works/<series>/books/book-01
    .venv/bin/python tools/repolish_book_v2.py works/<series>/books/book-01 --dry-run
    .venv/bin/python tools/repolish_book_v2.py works/<series>/books/book-01 --chapters 1,2

Unlike ``translate_book_v2.py``, this tool ignores whether a segment is already
translated: it selects every non-heading segment with a non-empty target and runs
the profile's polish skill over it against the source. Chapter state is backed up
under ``archive/repolish-<timestamp>/source/chapters/`` before the first batch of
each chapter is written. Failed batches leave the previous target untouched.
"""

from __future__ import annotations

import argparse
import json
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import batch_translate
import book_state
import polish_batch
import profile_rules
import segment_state


class TimeBudgetExceeded(RuntimeError):
    pass


def _chapter_paths(book_dir: Path, chapters: list[int | float] | None) -> list[Path]:
    paths = sorted(
        (book_dir / "source" / segment_state.CHAPTERS_DIR).glob("ch-*.json"),
        key=lambda path: segment_state.chapter_number(
            segment_state.load_chapter(path).get("chapter") or 0
        ),
    )
    if chapters is None:
        return paths
    wanted = {segment_state.chapter_number(number) for number in chapters}
    selected = []
    for path in paths:
        chapter = segment_state.load_chapter(path)
        if segment_state.chapter_number(chapter.get("chapter") or 0) in wanted:
            selected.append(path)
    return selected


def _polishable(chapter: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        segment
        for segment in chapter.get("segments") or []
        if segment.get("kind") not in ("heading", "separator")
        and isinstance(segment.get("target"), str)
        and segment["target"].strip()
    ]


def _append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def repolish_book(
    book_dir: Path,
    client: Any,
    *,
    chapters: list[int | float] | None = None,
    budget: int = batch_translate.DEFAULT_BATCH_BUDGET,
    max_retries: int = batch_translate.MAX_PROTOCOL_RETRIES,
    max_seconds: float | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    book_dir = Path(book_dir)
    rules = profile_rules.section_with_skill("polish", mode="轻改", work_dir=book_dir)
    glossary = book_state.load_glossary_terms(book_dir)
    paths = _chapter_paths(book_dir, chapters)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_dir = book_dir / "archive" / f"repolish-{timestamp}" / "source" / segment_state.CHAPTERS_DIR
    progress_path = book_dir / "work_temp" / "repolish" / "progress.jsonl"
    started = time.time()
    polished = failures = 0
    failure_rows: list[dict[str, Any]] = []
    stopped = False
    plan: list[dict[str, Any]] = []

    for path in paths:
        chapter = segment_state.load_chapter(path)
        selected = _polishable(chapter)
        plan.append(
            {
                "chapter": chapter.get("chapter"),
                "segments": len(selected),
                "title": chapter.get("title") or "",
            }
        )

    if dry_run:
        return {
            "book_dir": str(book_dir),
            "dry_run": True,
            "chapters": plan,
            "segments": sum(item["segments"] for item in plan),
            "backup_dir": None,
        }

    for path in paths:
        chapter_started = time.time()
        chapter = segment_state.load_chapter(path)
        selected = _polishable(chapter)
        chapter_polished = chapter_failures = 0
        if not selected:
            _append_jsonl(
                progress_path,
                {
                    "ts": time.time(),
                    "chapter": chapter.get("chapter"),
                    "segments": 0,
                    "polished": 0,
                    "failures": 0,
                    "elapsed": round(time.time() - chapter_started, 2),
                },
            )
            continue

        backup_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, backup_dir / path.name)

        def handle_batch(results: list[dict[str, Any]]) -> None:
            nonlocal polished, failures, chapter_polished, chapter_failures
            stats = polish_batch.apply_polished(chapter, results)
            polished += stats["applied"]
            chapter_polished += stats["applied"]
            errors = [result for result in results if result.get("error")]
            failures += len(errors)
            chapter_failures += len(errors)
            failure_rows.extend(
                {"chapter": chapter.get("chapter"), **result} for result in errors
            )
            segment_state.save_chapter(path, chapter)
            if max_seconds is not None and time.time() - started >= max_seconds:
                raise TimeBudgetExceeded()

        try:
            polish_batch.polish_batch(
                client,
                selected,
                budget=budget,
                max_retries=max_retries,
                rules=rules,
                glossary_terms=glossary,
                on_batch=handle_batch,
            )
        except TimeBudgetExceeded:
            stopped = True

        _append_jsonl(
            progress_path,
            {
                "ts": time.time(),
                "chapter": chapter.get("chapter"),
                "segments": len(selected),
                "polished": chapter_polished,
                "failures": chapter_failures,
                "elapsed": round(time.time() - chapter_started, 2),
            },
        )
        if stopped:
            break

    return {
        "book_dir": str(book_dir),
        "dry_run": False,
        "chapters": plan,
        "segments": sum(item["segments"] for item in plan),
        "polished": polished,
        "failures": failures,
        "failure_rows": failure_rows,
        "stopped": stopped,
        "backup_dir": str(backup_dir) if backup_dir.is_dir() else None,
        "elapsed": round(time.time() - started, 2),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("book_dir", type=Path)
    ap.add_argument(
        "--model",
        default="DeepSeek-V4.1-Flash-1017-TensorFold-Zig",
    )
    ap.add_argument("--effort", default="low")
    ap.add_argument("--max-tokens", type=int, default=32768)
    ap.add_argument("--budget", type=int, default=batch_translate.DEFAULT_BATCH_BUDGET)
    ap.add_argument("--max-retries", type=int, default=batch_translate.MAX_PROTOCOL_RETRIES)
    ap.add_argument("--chapters", default=None)
    ap.add_argument("--max-seconds", type=float, default=0)
    ap.add_argument("--dry-run", action="store_true")
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

    client = None
    if not args.dry_run:
        from model_api import make_client

        client = make_client(
            args.model,
            args.effort,
            args.max_tokens,
            usage_log=args.book_dir / "work_temp" / "usage.jsonl",
        )
    summary = repolish_book(
        args.book_dir,
        client,
        chapters=chapters,
        budget=args.budget,
        max_retries=args.max_retries,
        max_seconds=args.max_seconds or None,
        dry_run=args.dry_run,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if summary.get("failures"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
