#!/usr/bin/env python3
"""Convert a FicHub (fanfiction.net) HTML export into Markdown for the pipeline.

FicHub exports use a different layout from AO3's download HTML:

    <h1><span class="title">...</span></h1>
    <h2>by <span class="author">...</span></h2>
    <nav id="contents-list">...</nav>
    <div id="chap_1"><div class="chapter_nav">...</div><h2>...</h2><p>...</p>...</div>

This script writes the same layout as `ao3_convert.py`:

    works/<work-id>-<slug>/
        meta.json
        source/original.html
        source/work.md
        source/chapters/ch-NN.md

Usage:
    .venv/bin/python tools/fic_convert.py The_girl_.../*.html
    .venv/bin/python tools/fic_convert.py <file.html> --force
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

from bs4 import BeautifulSoup
from bs4.element import Tag

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ao3_convert import chapter_body, render_block, yaml_quote  # noqa: E402


def collapse(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def chapter_text(div: Tag) -> tuple[str, str]:
    """Return (heading, body) for one <div id="chap_N">."""
    heading_tag = div.find("h2")
    heading = heading_tag.get_text(" ", strip=True) if heading_tag else ""
    blocks = []
    for child in div.children:
        if not isinstance(child, Tag):
            continue
        classes = child.get("class") or []
        if "chapter_nav" in classes:
            continue
        if child.name.lower() == "h2":
            continue
        blocks.extend(render_block(child))
    body = "\n\n".join(b for b in blocks if b.strip())
    return heading, body


def parse_meta(soup: BeautifulSoup, html_path: Path) -> dict:
    title_tag = soup.find("h1")
    title = None
    if title_tag:
        span = title_tag.find(class_="title")
        title = (span or title_tag).get_text(" ", strip=True)
    author = None
    byline = soup.find("h2")
    if byline:
        span = byline.find(class_="author")
        if span:
            author = span.get_text(" ", strip=True)
        else:
            author = byline.get_text(" ", strip=True).removeprefix("by ").strip()

    summary = ""
    h2s = soup.find_all("h2")
    if h2s:
        nxt = h2s[0].find_next_sibling()
        while nxt is not None and not (isinstance(nxt, Tag) and nxt.name == "div"):
            nxt = nxt.find_next_sibling()
        if nxt is not None:
            summary = chapter_body(nxt)

    url = None
    for a in soup.find_all("a", href=True):
        if "fanfiction.net/s/" in a["href"]:
            url = a["href"]
            break
    m = re.search(r"/s/(\d+)", url or "")
    ffn_id = m.group(1) if m else None

    stats = {}
    for p in soup.find_all("p"):
        text = collapse(p.get_text(" ", strip=True))
        if text.startswith("Chapters:"):
            stats["Chapters"] = text.split(":", 1)[1].strip()
        elif text.startswith("Words:"):
            stats["Words"] = text.split(":", 1)[1].strip()
        elif text.startswith("Rated:"):
            for part in text.split(" - "):
                if part.startswith("Rated:"):
                    stats["Rated"] = part.split(":", 1)[1].strip()
                elif part.startswith("Genre:"):
                    stats["Genre"] = part.split(":", 1)[1].strip()
                elif part.startswith("Characters:"):
                    stats["Characters"] = part.split(":", 1)[1].strip()

    return {
        "work_id": f"ffn-{ffn_id}" if ffn_id else None,
        "url": url,
        "source_site": "fanfiction.net",
        "title": title,
        "author": author,
        "rating": stats.get("Rated"),
        "genre": stats.get("Genre"),
        "characters": stats.get("Characters"),
        "language": "English",
        "stats": stats,
        "summary": summary,
        "source_file": str(html_path),
        "converted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def slugify(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return slug or "untitled"


def convert(html_path: Path, root: Path, force: bool = False) -> Path:
    soup = BeautifulSoup(html_path.read_text(encoding="utf-8"), "html.parser")
    meta = parse_meta(soup, html_path)

    chapters = []
    for div in soup.find_all("div", id=re.compile(r"^chap_\d+$")):
        heading, body = chapter_text(div)
        if body.strip():
            chapters.append({"title": heading, "body": body})
    if not chapters:
        sys.exit(f"No chapters found in {html_path}")

    wid = meta["work_id"] or "unknown"
    slug = slugify(meta["title"] or html_path.stem)
    work_dir = root / f"{wid}-{slug}"
    if work_dir.exists() and not force:
        sys.exit(f"{work_dir} already exists; rerun with --force to overwrite")

    source = work_dir / "source"
    chapters_dir = source / "chapters"
    chapters_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(html_path, source / "original.html")

    merged_parts = []
    chapter_records = []
    for i, ch in enumerate(chapters, 1):
        heading = ch["title"] or f"Chapter {i}"
        text = f"# {heading}\n\n{ch['body']}"
        (chapters_dir / f"ch-{i:02d}.md").write_text(text + "\n", encoding="utf-8")
        merged_parts.append(text)
        chapter_records.append(
            {
                "index": i,
                "title": heading,
                "words": len(re.findall(r"[A-Za-z']+", ch["body"])),
                "chars": len(text),
            }
        )

    front = [
        "---",
        f"title: {yaml_quote(meta['title'] or '')}",
        f"author: {yaml_quote(meta['author'] or '')}",
        f"source: {yaml_quote(meta['url'] or '')}",
        f"language: {yaml_quote(meta['language'] or 'en')}",
        "---",
        "",
    ]
    (source / "work.md").write_text(
        "\n".join(front) + "\n\n".join(merged_parts) + "\n", encoding="utf-8"
    )

    meta["chapters"] = chapter_records
    meta["chapter_count"] = len(chapter_records)
    meta["word_count"] = sum(c["words"] for c in chapter_records)
    (work_dir / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return work_dir


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("html", type=Path, help="FicHub downloaded HTML file")
    ap.add_argument("--root", type=Path, default=Path("works"), help="output root (default: works)")
    ap.add_argument("--force", action="store_true", help="overwrite an existing work directory")
    args = ap.parse_args()
    if not args.html.is_file():
        sys.exit(f"Not found: {args.html}")
    work_dir = convert(args.html, args.root, args.force)
    meta = json.loads((work_dir / "meta.json").read_text(encoding="utf-8"))
    print(f"{work_dir}")
    print(f"  title:    {meta['title']}")
    print(f"  author:   {meta['author']}")
    print(f"  chapters: {meta['chapter_count']}  words: {meta['word_count']}")
    print(f"  md:       {work_dir}/source/work.md")


if __name__ == "__main__":
    main()
