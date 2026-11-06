#!/usr/bin/env python3
"""Create a series/book and ingest source files into v2 chapter state.

Usage:
    .venv/bin/python tools/ingest_book_v2.py \
        --series-root works/hp-tsundere-1667660966 \
        --series-id hp-tsundere-1667660966 \
        --title "Harry Potter and the Sorcerer's Tsundere" \
        --author GuardBro \
        --book 1 --title-book "Book 1" \
        --source "…/001 Book 1: Chapter 5-7.txt" "…/002 Book 1: Chapters 8-12.txt" \
        --complete

The command creates the series root if missing, creates the book sub-work if
missing, and ingests all listed sources into one continuous chapter set. It does
not translate; run translate_book_v2.py next.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import book_state
import segment_state


def ingest_book(
    series_root: Path,
    *,
    series_id: str,
    title: str,
    author: str,
    book_number: int,
    sources: list[Path],
    book_title: str = "",
    source_url: str = "",
    chapter_offset: int = 0,
    complete: bool = True,
) -> dict:
    if not book_state.is_series_root(series_root):
        book_state.create_series(
            series_root,
            series_id=series_id,
            title=title,
            author=author,
            source_url=source_url,
            books=[{"number": book_number, "title": book_title}],
        )
    book = book_state.create_book(series_root, book_number, title=book_title)
    paths = segment_state.ingest_sources(
        book,
        sources,
        chapter_offset=chapter_offset,
        complete=complete,
    )
    meta = json.loads((book / "meta.json").read_text(encoding="utf-8"))
    return {
        "series_root": str(series_root),
        "book_dir": str(book),
        "chapter_files": [str(path) for path in paths],
        "chapter_count": meta.get("chapter_count", 0),
        "source_complete": meta.get("source_complete", False),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--series-root", type=Path, required=True)
    ap.add_argument("--series-id", required=True)
    ap.add_argument("--title", required=True)
    ap.add_argument("--author", required=True)
    ap.add_argument("--source-url", default="")
    ap.add_argument("--book", type=int, required=True)
    ap.add_argument("--title-book", default="")
    ap.add_argument("--source", type=Path, nargs="+", required=True)
    ap.add_argument("--chapter-offset", type=int, default=0)
    ap.add_argument("--incomplete", action="store_true")
    args = ap.parse_args()

    summary = ingest_book(
        args.series_root,
        series_id=args.series_id,
        title=args.title,
        author=args.author,
        book_number=args.book,
        sources=args.source,
        book_title=args.title_book,
        source_url=args.source_url,
        chapter_offset=args.chapter_offset,
        complete=not args.incomplete,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
