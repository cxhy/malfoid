#!/usr/bin/env python3
"""Merge per-chunk polished Markdown into translation/polished/book.md.

Usage:
    .venv/bin/python tools/merge_polished.py works/<work>
    .venv/bin/python tools/merge_polished.py works/<work> --allow-missing

Chunks are merged in numeric order (chunk0001, chunk0002, ...). The source of
truth is work_temp/chunk*.md: every source chunk must have a polished chunk
unless --allow-missing is passed, which is useful right after an incremental
update before the pending chunks are polished.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


def natural(cid: str) -> int:
    m = re.search(r"(\d+)$", cid)
    return int(m.group(1)) if m else 0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("work_dir", type=Path)
    ap.add_argument("--allow-missing", action="store_true")
    args = ap.parse_args()

    work = args.work_dir
    temp = work / "work_temp"
    polished = work / "translation" / "polished"
    source_ids = sorted(
        (p.stem for p in temp.glob("chunk*.md") if not p.name.startswith("output_")), key=natural
    )
    if not source_ids:
        sys.exit(f"no source chunks in {temp}")

    missing = [cid for cid in source_ids if not (polished / f"{cid}.md").is_file()]
    if missing and not args.allow_missing:
        sys.exit(f"missing polished chunks ({len(missing)}): {', '.join(missing[:10])}")

    parts = []
    for cid in source_ids:
        p = polished / f"{cid}.md"
        if not p.is_file():
            continue
        text = p.read_text(encoding="utf-8").strip()
        if text:
            parts.append(text)
    book = polished / "book.md"
    book.write_text("\n\n".join(parts) + "\n", encoding="utf-8")
    print(f"merged {len(parts)}/{len(source_ids)} chunks -> {book}")
    if missing:
        print(f"warning: {len(missing)} chunk(s) missing polished output")


if __name__ == "__main__":
    main()
