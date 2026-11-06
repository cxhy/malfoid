#!/usr/bin/env python3
"""Structural check for a polished chunk after manual/proofreading edits.

Usage:
    .venv/bin/python tools/check_polished.py <work_dir> chunk0001 [chunk0002 ...]

Compares translation/polished/<chunk>.md against the original translation
work_temp/output_<chunk>.md using the same checks polish_chunk.py applies:
heading count, block count, Chinese length ratio, glossary terms, forbidden
names, English residue. Prints OK or the list of problems; exits 1 on problems.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from polish_chunk import check  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("work_dir", type=Path)
    ap.add_argument("chunks", nargs="+")
    args = ap.parse_args()

    work = args.work_dir
    temp = work / "work_temp"
    terms = json.loads((temp / "glossary.json").read_text(encoding="utf-8"))["terms"]

    bad = []
    for cid in args.chunks:
        old = (temp / f"output_{cid}.md").read_text(encoding="utf-8")
        new_path = work / "translation" / "polished" / f"{cid}.md"
        if not new_path.is_file():
            print(f"{cid}: MISSING {new_path}")
            bad.append(cid)
            continue
        new = new_path.read_text(encoding="utf-8")
        problems = check(old, new, terms)
        if problems:
            print(f"{cid}: WARN " + "; ".join(problems))
            bad.append(cid)
        else:
            print(f"{cid}: OK")
    if bad:
        sys.exit(1)


if __name__ == "__main__":
    main()
