#!/usr/bin/env python3
"""Convert a SpaceBattles plain-text scrape into Markdown for pipeline v2.

SpaceBattles scrapes in this repo are plain .txt files with a header such as:

    Worst Enemy [Harry Potter x F!Draco Malfoy]
    第 1 章　First Year
    ==================================================

The v2 ingestion parser only recognises ``Ch N`` marker lines and Markdown
headings, so this converter normalises each part into ``# <title>`` chapters,
drops the scrape-only book-title line and ``====`` rule, and keeps scene breaks
(``—``) and paragraphs intact.

Output layout (default ``<input parent>/normalized``):

    normalized/
        original/                 untouched copies of the input .txt files
        book-01/ch-01.md          one Markdown file per chapter, grouped by year
        book-02/ch-01.md
        ...
        manifest.json             source file -> book/chapter mapping

Usage:
    .venv/bin/python tools/sb_convert.py "Worst Enemy/单章"
    .venv/bin/python tools/sb_convert.py "Worst Enemy/单章" --out "Worst Enemy/normalized" --force
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

CHAPTER_MARKER = re.compile(r"^\s*第\s*(\d+)\s*章[\s　]*(.*?)\s*$")
RULE = re.compile(r"^\s*=+\s*$")
YEAR = re.compile(r"^\s*(First|Second|Third|Fourth|Fifth|Sixth|Seventh)\b", re.IGNORECASE)

YEAR_BOOK = {
    "first": 1,
    "second": 2,
    "third": 3,
    "fourth": 4,
    "fifth": 5,
    "sixth": 6,
    "seventh": 7,
}


def parse_part(path: Path) -> dict:
    """Return serial, title and normalised body for one scraped part."""
    lines = path.read_text(encoding="utf-8").splitlines()
    marker_index = None
    serial = None
    title = ""
    for index, line in enumerate(lines):
        match = CHAPTER_MARKER.match(line)
        if match:
            marker_index = index
            serial = int(match.group(1))
            title = match.group(2).strip()
            break
    if marker_index is None or serial is None:
        raise ValueError(f"no '第 N 章' marker in {path}")

    paragraphs: list[str] = []
    current: list[str] = []
    for line in lines[marker_index + 1 :]:
        if RULE.match(line):
            continue
        if line.strip() == "":
            if current:
                paragraphs.append(" ".join(current))
                current = []
            continue
        current.append(line.strip())
    if current:
        paragraphs.append(" ".join(current))

    body = "\n\n".join(re.sub(r"\s+", " ", paragraph).strip() for paragraph in paragraphs)
    year_match = YEAR.match(title)
    if not year_match:
        raise ValueError(f"cannot infer school year from chapter title {title!r} in {path}")
    book = YEAR_BOOK[year_match.group(1).lower()]
    return {
        "source": str(path),
        "source_name": path.name,
        "serial": serial,
        "title": title,
        "book": book,
        "body": body,
    }


def convert(input_path: Path, out_dir: Path, force: bool = False) -> dict:
    if input_path.is_dir():
        files = sorted(p for p in input_path.iterdir() if p.suffix.lower() == ".txt")
    else:
        files = [input_path]
    if not files:
        sys.exit(f"No .txt files found in {input_path}")

    if out_dir.exists() and any(out_dir.iterdir()) and not force:
        sys.exit(f"{out_dir} already exists and is not empty; rerun with --force to overwrite")

    parts = [parse_part(path) for path in files]
    serials = [part["serial"] for part in parts]
    if len(set(serials)) != len(serials):
        sys.exit("duplicate chapter markers across input files")

    originals = out_dir / "original"
    originals.mkdir(parents=True, exist_ok=True)
    if force:
        for old in originals.glob("*.txt"):
            old.unlink()
    for path in files:
        shutil.copy2(path, originals / path.name)

    counters: dict[int, int] = {}
    records = []
    for part in sorted(parts, key=lambda item: item["serial"]):
        book = part["book"]
        counters[book] = counters.get(book, 0) + 1
        chapter_number = counters[book]
        book_dir = out_dir / f"book-{book:02d}"
        book_dir.mkdir(parents=True, exist_ok=True)
        output = book_dir / f"ch-{chapter_number:02d}.md"
        output.write_text(f"# {part['title']}\n\n{part['body']}\n", encoding="utf-8")
        records.append(
            {
                "book": book,
                "book_chapter": chapter_number,
                "source_serial": part["serial"],
                "title": part["title"],
                "source_file": part["source"],
                "output_file": str(output),
                "chars": len(part["body"]),
            }
        )

    manifest = {
        "title": "Worst Enemy",
        "author": "lb_n0o",
        "source": "https://forums.spacebattles.com/threads/worst-enemy-harry-potter-x-f-draco-malfoy.1343487/",
        "converted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "input": str(input_path),
        "chapters": records,
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("input", type=Path, help="scraped .txt file or directory of parts")
    ap.add_argument("--out", type=Path, default=None, help="output dir (default: <input parent>/normalized)")
    ap.add_argument("--force", action="store_true", help="overwrite an existing output dir")
    args = ap.parse_args()
    if not args.input.exists():
        sys.exit(f"Not found: {args.input}")
    out_dir = args.out or (args.input.parent / "normalized")
    manifest = convert(args.input, out_dir, args.force)

    by_book: dict[int, list[dict]] = {}
    for record in manifest["chapters"]:
        by_book.setdefault(record["book"], []).append(record)
    print(out_dir)
    print(f"  chapters: {len(manifest['chapters'])}")
    for book in sorted(by_book):
        records = by_book[book]
        chars = sum(record["chars"] for record in records)
        print(f"  book-{book:02d}: {len(records)} chapters, {chars} chars")


if __name__ == "__main__":
    main()
