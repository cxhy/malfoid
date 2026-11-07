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
import polish_batch
import segment_state
from profile_rules import section


def load_glossary_terms(book_dir: Path) -> list[dict[str, Any]]:
    """Load the book glossary, falling back to the series master."""
    candidates = [book_dir / "work_temp" / "glossary.json"]
    series_root = book_dir.parent.parent
    candidates.append(series_root / "glossary.json")
    for path in candidates:
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        terms = data.get("terms") if isinstance(data, dict) else None
        return [term for term in terms or [] if isinstance(term, dict)]
    return []


def _chapter_paths(book_dir: Path, chapters: list[int] | None = None) -> list[Path]:
    chapters_dir = book_dir / "source" / segment_state.CHAPTERS_DIR
    paths = sorted(chapters_dir.glob("ch-*.json"), key=lambda p: int(p.stem.split("-")[-1]))
    if chapters is not None:
        wanted = set(chapters)
        paths = [path for path in paths if int(path.stem.split("-")[-1]) in wanted]
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
    chapters: list[int] | None = None,
    progress_path: Path | None = None,
) -> dict[str, Any]:
    """Translate pending segments and optionally polish them; return a summary."""
    terms = load_glossary_terms(book_dir)
    translation_rules = section("translation", work_dir=book_dir)
    polish_rules = section("polish", work_dir=book_dir)
    translated = polished = 0
    failures: list[dict[str, Any]] = []
    context = ""
    progress_path = progress_path or book_dir / "work_temp" / "progress.jsonl"
    for path in _chapter_paths(book_dir, chapters):
        chapter_started = time.time()
        chapter = segment_state.load_chapter(path)
        pending = _pending(chapter.get("segments") or [])
        chapter_translated = chapter_polished = chapter_failures = 0
        if pending:
            results = batch_translate.translate_batch(
                client,
                pending,
                budget=budget,
                max_retries=max_retries,
                glossary_terms=terms,
                context=context,
                rules=translation_rules,
            )
            stats = batch_translate.apply_targets(chapter, results)
            translated += stats["applied"]
            chapter_translated += stats["applied"]
            chapter_results = [result for result in results if result.get("error")]
            chapter_failures += len(chapter_results)
            failures.extend(
                {"chapter": chapter.get("chapter"), **result} for result in chapter_results
            )
            if polish:
                to_polish = [
                    segment for segment in pending if (segment.get("target") or "").strip()
                ]
                if to_polish:
                    polished_results = polish_batch.polish_batch(
                        client,
                        to_polish,
                        budget=budget,
                        max_retries=max_retries,
                        rules=polish_rules,
                    )
                    polished_stats = polish_batch.apply_polished(chapter, polished_results)
                    polished += polished_stats["applied"]
                    chapter_polished += polished_stats["applied"]
                    polish_errors = [result for result in polished_results if result.get("error")]
                    chapter_failures += len(polish_errors)
                    failures.extend(
                        {"chapter": chapter.get("chapter"), **result} for result in polish_errors
                    )
            segment_state.save_chapter(path, chapter)
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
        context = _chapter_tail(chapter)
    return {
        "book_dir": str(book_dir),
        "translated": translated,
        "polished": polished,
        "failures": failures,
        "polish": polish,
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
    ap.add_argument("--no-polish", action="store_true")
    ap.add_argument(
        "--chapters",
        default=None,
        help="comma-separated chapter numbers to translate (default: all)",
    )
    args = ap.parse_args()
    chapters = None
    if args.chapters:
        chapters = [int(value) for value in args.chapters.split(",") if value.strip()]

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
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if summary["failures"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
