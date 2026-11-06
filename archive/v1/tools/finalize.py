#!/usr/bin/env python3
"""Assemble the final reader-facing HTML for a work.

Usage:
    .venv/bin/python tools/finalize.py works/<work-dir> [more work dirs]

Layout produced under <work>/translation/:
    <Original_Title>_Vol_<N>.html   one reader-facing HTML per school year,
                                    when split_volumes.py has produced vol_*.html
    <Original_Title>.html           single-book delivery when the work has
                                    no per-year volumes
    draft/                          unpolished merged markdown/html (intermediate)
    polished/                       per-chunk polished markdown + merged book.md/html

Volume HTMLs take precedence: if translation/polished/vol_*.html exist, each is
copied to translation/<Title>_Vol_<N>.html and any legacy combined
<Title>.html is moved into draft/. Otherwise the combined book.html is copied
to translation/<Title>.html. Intermediate artifacts already in translation/
(output.md, output.html, book.html, book_doc.html) are moved into draft/ so the
reader-facing HTML is the only file at the top level.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
from pathlib import Path


def title_slug(title: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "_", title).strip("_")
    return slug or "untitled"


def finalize(work: Path) -> list[Path]:
    meta = json.loads((work / "meta.json").read_text(encoding="utf-8"))
    title = meta.get("title") or work.name
    trans = work / "translation"
    polished = trans / "polished"

    draft = trans / "draft"
    draft.mkdir(exist_ok=True)
    for name in ("output.md", "output.html", "book.html", "book_doc.html"):
        p = trans / name
        if p.is_file():
            shutil.move(str(p), str(draft / name))

    slug = title_slug(title)
    volumes = sorted(polished.glob("vol_*.html"))
    if volumes:
        # Volume-based delivery: HTML and Markdown per school year.
        dests = []
        for vol in volumes:
            stem = vol.stem.replace("vol_", "Vol_")
            html_dest = trans / f"{slug}_{stem}.html"
            shutil.copyfile(vol, html_dest)
            dests.append(html_dest)
            md_src = vol.with_suffix(".md")
            if md_src.is_file():
                md_dest = trans / f"{slug}_{stem}.md"
                shutil.copyfile(md_src, md_dest)
                dests.append(md_dest)
        for legacy in (trans / f"{slug}.html", trans / f"{slug}.md"):
            if legacy.is_file():
                shutil.move(str(legacy), str(draft / legacy.name))
        return dests

    src_html = polished / "book.html"
    src_md = polished / "book.md"
    if not src_html.is_file():
        raise FileNotFoundError(f"missing polished HTML: {src_html}")
    dests = []
    html_dest = trans / f"{slug}.html"
    shutil.copyfile(src_html, html_dest)
    dests.append(html_dest)
    if src_md.is_file():
        md_dest = trans / f"{slug}.md"
        shutil.copyfile(src_md, md_dest)
        dests.append(md_dest)
    return dests


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("works", type=Path, nargs="+")
    args = ap.parse_args()
    for work in args.works:
        dests = finalize(work)
        for dest in dests:
            print(f"{work.name}: {dest}")


if __name__ == "__main__":
    main()
