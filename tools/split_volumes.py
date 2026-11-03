#!/usr/bin/env python3
"""Split a multi-year Harry Potter fic into one volume per school year.

Usage:
    .venv/bin/python tools/split_volumes.py works/<work-dir> [more work dirs]

Reads <work>/translation/polished/book.md and groups its level-1 chapter
headings by school year (Chinese "一年级/二年级/..." or English "Year N").
An interlude ("间章"/"Interlude") is attached to the volume of the year that
follows it (as that volume's opening chapter); a trailing interlude joins the
previous year.

Outputs per volume:
    translation/polished/vol_<N>.md
    translation/polished/vol_<N>.html
    translation/<Original_Title>_Vol_<N>.html   (reader-facing copy)

A work with fewer than two detected years is left alone: its existing single
book.html is the delivery unit. Run tools/finalize.py afterwards to move the
old combined artifacts into translation/draft/.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_html import build_from  # noqa: E402
from finalize import title_slug  # noqa: E402

CN_NUM = ["零", "一", "二", "三", "四", "五", "六", "七", "八", "九", "十"]
CN_YEAR = {
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}
YEAR_LABEL = {n: f"{CN_NUM[n]}年级" for n in range(1, 11)}
INTERLUDE = re.compile(r"^(间章|插曲|Interlude|interlude)\b")
CN_YEAR_RE = re.compile(r"^([一二三四五六七八九十])年级")
EN_YEAR_RE = re.compile(r"^Year\s+(\d+)\b", re.IGNORECASE)


def chapter_year(heading: str) -> int | None:
    h = heading.lstrip("# ").strip()
    m = CN_YEAR_RE.match(h)
    if m:
        return CN_YEAR[m.group(1)]
    m = EN_YEAR_RE.match(h)
    if m:
        return int(m.group(1))
    return None


def is_interlude(heading: str) -> bool:
    h = heading.lstrip("# ").strip()
    return bool(INTERLUDE.match(h))


def split_chapters(md: str) -> list[tuple[str, str]]:
    lines = md.splitlines()
    chapters: list[list[str]] = []
    for line in lines:
        if line.startswith("# "):
            chapters.append([line])
        elif chapters:
            chapters[-1].append(line)
    return [("\n".join(c).strip() + "\n", c[0][2:].strip()) for c in chapters]


def group_volumes(chapters: list[tuple[str, str]]) -> dict[int, list[str]]:
    years = [chapter_year(h) for _, h in chapters]
    volumes: dict[int, list[str]] = {}
    for i, (body, heading) in enumerate(chapters):
        year = years[i]
        if year is None and is_interlude(heading):
            nxt = next((y for y in years[i + 1 :] if y is not None), None)
            year = (
                nxt
                if nxt is not None
                else next((y for y in reversed(years[:i]) if y is not None), None)
            )
        if year is None:
            continue
        volumes.setdefault(year, []).append(body)
    return volumes


def split_work(work: Path) -> list[Path]:
    meta = json.loads((work / "meta.json").read_text(encoding="utf-8"))
    title = meta.get("title") or work.name
    polished = work / "translation" / "polished"
    book = polished / "book.md"
    if not book.is_file():
        raise FileNotFoundError(f"missing polished markdown: {book}")

    chapters = split_chapters(book.read_text(encoding="utf-8"))
    volumes = group_volumes(chapters)
    if len(volumes) < 2:
        print(f"{work.name}: only {len(volumes)} year volume(s) detected — left as a single book")
        return []

    trans = work / "translation"
    slug = title_slug(title)
    written = []
    for year in sorted(volumes):
        label = YEAR_LABEL.get(year, f"Year {year}")
        vol_title = f"{title} 第{CN_NUM[year] if year < len(CN_NUM) else year}卷（{label}）"
        md = polished / f"vol_{year}.md"
        md.write_text("\n\n".join(volumes[year]).strip() + "\n", encoding="utf-8")
        html = build_from(work, md, polished / f"vol_{year}.html", vol_title)
        dest = trans / f"{slug}_Vol_{year}.html"
        dest.write_bytes(html.read_bytes())
        written.append(dest)
        print(f"{work.name}: {dest} ({len(volumes[year])} chapter block(s), {label})")
    return written


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("works", type=Path, nargs="+")
    args = ap.parse_args()
    for work in args.works:
        split_work(work)


if __name__ == "__main__":
    main()
