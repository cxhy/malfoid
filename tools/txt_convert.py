#!/usr/bin/env python3
"""Convert Wattpad plain-text exports into Markdown for the translation pipeline.

Wattpad exports in this repo are plain .txt files where each serialized part
starts with a line such as:

    === Book 1: Chapter 5-7 ===

This script writes the same layout as `ao3_convert.py` / `fic_convert.py`:

    works/<work-id>-<slug>/
        meta.json                 work metadata + chapter list
        source/original/          untouched copies of the input .txt files
        source/work.md            merged Markdown (input for translate-book convert.py)
        source/chapters/ch-NN.md  one file per serialized part

Chapter headings are `# Chapter <n>: <part title>`, so prescan/chunk mapping
works unchanged.

Usage:
    .venv/bin/python tools/txt_convert.py "Harry Potter and the Sorcerer's Tsundere/单章"
    .venv/bin/python tools/txt_convert.py "…/单章/001 Book 1: Chapter 5-7.txt" --work-id wattpad-1667660966
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

MARKER = re.compile(r"^\s*===\s*(.*?)\s*===\s*$")


def slugify(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return slug or "untitled"


def yaml_quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def parse_part(path: Path) -> tuple[str, str]:
    """Return (title, body) for one Wattpad part."""
    lines = path.read_text(encoding="utf-8").splitlines()
    title = path.stem
    body_lines = []
    marker_seen = False
    for line in lines:
        m = MARKER.match(line)
        if m:
            if not marker_seen:
                title = m.group(1).strip() or title
                marker_seen = True
            continue
        body_lines.append(line)
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", "\n".join(body_lines)) if p.strip()]
    body = "\n\n".join(p.replace("\n", " ").strip() for p in paragraphs)
    return title, body


def convert(
    input_path: Path,
    root: Path,
    work_id: str,
    title: str,
    author: str,
    url: str,
    force: bool = False,
) -> Path:
    if input_path.is_dir():
        files = sorted(p for p in input_path.iterdir() if p.suffix.lower() == ".txt")
    else:
        files = [input_path]
    if not files:
        sys.exit(f"No .txt files found in {input_path}")

    slug = slugify(title)
    work_dir = root / f"{work_id}-{slug}"
    if work_dir.exists() and not force:
        sys.exit(f"{work_dir} already exists; rerun with --force to overwrite")

    source = work_dir / "source"
    chapters_dir = source / "chapters"
    originals = source / "original"
    chapters_dir.mkdir(parents=True, exist_ok=True)
    originals.mkdir(parents=True, exist_ok=True)

    merged_parts = []
    chapter_records = []
    for i, path in enumerate(files, 1):
        part_title, body = parse_part(path)
        if not body:
            continue
        heading = f"Chapter {i}: {part_title}"
        text = f"# {heading}\n\n{body}"
        (chapters_dir / f"ch-{i:02d}.md").write_text(text + "\n", encoding="utf-8")
        shutil.copy2(path, originals / path.name)
        merged_parts.append(text)
        chapter_records.append(
            {
                "index": i,
                "title": part_title,
                "words": len(re.findall(r"[A-Za-z']+", body)),
                "chars": len(text),
            }
        )

    front = [
        "---",
        f"title: {yaml_quote(title)}",
        f"author: {yaml_quote(author)}",
        f"source: {yaml_quote(url)}",
        f"language: {yaml_quote('English')}",
        "---",
        "",
    ]
    (source / "work.md").write_text(
        "\n".join(front) + "\n\n".join(merged_parts) + "\n", encoding="utf-8"
    )

    meta = {
        "work_id": work_id,
        "url": url,
        "title": title,
        "author": author,
        "rating": None,
        "warnings": None,
        "categories": ["F/M"],
        "fandoms": ["Harry Potter - J. K. Rowling"],
        "relationships": ["Draconia Malfoy/Harry Potter"],
        "characters": ["Draconia Malfoy", "Harry Potter"],
        "tags": ["Alternate Universe - Gender Changes", "Fem Draco Malfoy", "Malfoid"],
        "language": "English",
        "series": "",
        "stats": None,
        "summary": "哈利·波特系列改写：德拉科·马尔福性转为德拉科妮娅·马尔福（Draconia Malfoy）。",
        "source_file": str(input_path),
        "converted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "chapters": chapter_records,
        "chapter_count": len(chapter_records),
        "word_count": sum(c["words"] for c in chapter_records),
    }
    (work_dir / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return work_dir


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("input", type=Path, help="Wattpad .txt file or directory of parts")
    ap.add_argument("--root", type=Path, default=Path("works"), help="output root (default: works)")
    ap.add_argument("--work-id", default="wattpad-1667660966", help="work id prefix")
    ap.add_argument("--title", default="Harry Potter and the Sorcerer's Tsundere")
    ap.add_argument("--author", default="GuardBro")
    ap.add_argument("--url", default="https://www.wattpad.com/story/1667660966")
    ap.add_argument("--force", action="store_true", help="overwrite an existing work directory")
    args = ap.parse_args()
    if not args.input.exists():
        sys.exit(f"Not found: {args.input}")
    work_dir = convert(
        args.input, args.root, args.work_id, args.title, args.author, args.url, args.force
    )
    meta = json.loads((work_dir / "meta.json").read_text(encoding="utf-8"))
    print(f"{work_dir}")
    print(f"  title:    {meta['title']}")
    print(f"  author:   {meta['author']}")
    print(f"  chapters: {meta['chapter_count']}  words: {meta['word_count']}")
    print(f"  md:       {work_dir}/source/work.md")


if __name__ == "__main__":
    main()
