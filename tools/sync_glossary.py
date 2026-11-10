#!/usr/bin/env python3
"""Synchronize book glossaries from the series master glossary.

Usage:
    .venv/bin/python tools/sync_glossary.py works/<series>/books/book-01
    .venv/bin/python tools/sync_glossary.py works/<series>
    .venv/bin/python tools/sync_glossary.py works/<series> --merge-back

Resolution is global -> series -> book. The series master is authoritative for
terms it defines, but the global canonical table wins on canonical targets unless
a series term sets ``override_global``. Book-only terms are kept, and missing
additive fields are filled in. ``--merge-back`` first merges book-local terms into
the series master (never the global table), reports conflicts, then synchronizes
every book.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import book_state


def _book_number(book: Path) -> int | None:
    meta_path = book / "meta.json"
    if meta_path.is_file():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            number = meta.get("book_number")
            if isinstance(number, int):
                return number
        except (OSError, ValueError):
            pass
    name = book.name
    if name.startswith("book-") and name[5:].isdigit():
        return int(name[5:])
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path, help="book directory or series root")
    parser.add_argument(
        "--merge-back",
        action="store_true",
        help="merge book-local terms into the series master before syncing",
    )
    args = parser.parse_args()

    path = args.path
    books: list[Path] = []
    if book_state.is_series_root(path):
        series = book_state.load_series(path)
        for entry in series.get("books") or []:
            number = entry.get("number")
            if isinstance(number, int):
                try:
                    books.append(book_state.resolve_book(path, number))
                except FileNotFoundError:
                    continue
    else:
        books.append(path)

    merges = []
    if args.merge_back:
        for book in books:
            number = _book_number(book)
            if number is None:
                merges.append(
                    {
                        "book_dir": str(book),
                        "error": "cannot determine book number",
                    }
                )
                continue
            root = book.parent.parent
            try:
                counts = book_state.merge_glossary_back(root, number)
            except FileNotFoundError as exc:
                merges.append({"book_dir": str(book), "error": str(exc)})
                continue
            merges.append({"book_dir": str(book), "merge": counts})

    results = []
    for book in books:
        glossary_path = book_state.sync_glossary(book)
        terms = book_state.load_glossary_terms(book)
        results.append(
            {
                "book_dir": str(book),
                "glossary": str(glossary_path),
                "terms": len(terms),
            }
        )
    payload = {"books": results}
    series_root = path if book_state.is_series_root(path) else path.parent.parent
    if book_state.is_series_root(series_root):
        global_terms = book_state.load_global_terms(series_root)
        series_terms = list(
            book_state._load_master(series_root).get("terms") or []
        )
        _, global_conflicts = book_state.merge_global_series(global_terms, series_terms)
        payload["global_conflicts"] = global_conflicts
    if args.merge_back:
        payload["merge_back"] = merges
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
