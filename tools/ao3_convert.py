#!/usr/bin/env python3
"""Convert an AO3 "Download -> HTML" file into Markdown for the translation pipeline.

Output layout (default root: ./works):
    works/<work-id>-<slug>/
        meta.json                 work metadata + chapter list
        source/original.html      untouched copy of the input
        source/work.md            merged Markdown (input for translate-book convert.py)
        source/chapters/ch-NN.md  one file per chapter (QA / term extraction)

Usage:
    .venv/bin/python tools/ao3_convert.py Green_and_Silver/Green_and_Silver_Book_I.html
    .venv/bin/python tools/ao3_convert.py <file.html> --force
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
from bs4.element import NavigableString, Tag

BLOCK_TAGS = {"p", "blockquote", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "hr", "div", "pre"}
SKIP_TAGS = {"script", "style", "head", "title"}


def collapse(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def inline(node) -> str:
    """Render inline content of a tag to Markdown."""
    out = []
    for child in node.children:
        if isinstance(child, NavigableString):
            out.append(collapse(str(child)))
            continue
        if not isinstance(child, Tag):
            continue
        name = child.name.lower()
        if name in SKIP_TAGS:
            continue
        if name == "br":
            out.append("  \n")
            continue
        if name == "img":
            src = child.get("src", "")
            alt = child.get("alt", "")
            out.append(f"![{alt}]({src})")
            continue
        inner = inline(child)
        if name in ("em", "i"):
            inner = inner.strip()
            out.append(f"*{inner}*" if inner else "")
        elif name in ("strong", "b"):
            inner = inner.strip()
            out.append(f"**{inner}**" if inner else "")
        elif name == "a":
            href = child.get("href", "")
            inner = inner.strip()
            out.append(f"[{inner}]({href})" if href and inner else inner)
        elif name in ("code", "tt"):
            out.append(f"`{inner.strip()}`" if inner.strip() else "")
        else:
            out.append(inner)
    text = "".join(out)
    # tidy spaces around hard breaks and emphasis markers
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    text = re.sub(r" {2,}", " ", text)
    text = re.sub(r" ?\* ?", "*", text) if text.count("*") % 2 == 0 else text
    return text.strip()


def render_block(el: Tag, indent: str = "") -> list[str]:
    """Render a block-level tag into a list of Markdown blocks."""
    name = el.name.lower()
    if name in SKIP_TAGS:
        return []
    if name == "hr":
        return ["---"]
    if name in ("h1", "h2", "h3", "h4", "h5", "h6"):
        level = int(name[1])
        text = inline(el).replace("\n", " ").strip()
        return [f"{'#' * level} {text}"] if text else []
    if name == "p":
        text = inline(el)
        return [text] if text else []
    if name == "blockquote":
        inner = "\n\n".join(render_children(el))
        return ["\n".join("> " + line if line else ">" for line in inner.split("\n"))]
    if name == "pre":
        return ["```\n" + el.get_text() + "\n```"]
    if name in ("ul", "ol"):
        items = []
        for i, li in enumerate(el.find_all("li", recursive=False), 1):
            marker = "-" if name == "ul" else f"{i}."
            body = "\n".join(render_children(li)).strip()
            body = body.replace("\n\n", "\n").replace("\n", "\n  ")
            items.append(f"{marker} {body}")
        return ["\n".join(items)]
    if name == "div":
        return render_children(el)
    return [inline(el)]


def render_children(el: Tag) -> list[str]:
    blocks = []
    for child in el.children:
        if isinstance(child, NavigableString):
            text = collapse(str(child)).strip()
            if text:
                blocks.append(text)
            continue
        if isinstance(child, Tag):
            blocks.extend(render_block(child))
    return blocks


def chapter_body(div: Tag) -> str:
    blocks = render_children(div)
    return "\n\n".join(b for b in blocks if b.strip())


def parse_meta(preface: Tag) -> dict:
    meta = {}
    for dl in preface.find_all("dl"):
        for dt in dl.find_all("dt"):
            key = dt.get_text(" ", strip=True).rstrip(":").strip()
            dd = dt.find_next_sibling("dd")
            if not dd:
                continue
            value = dd.get_text(" ", strip=True)
            if key == "Stats":
                stats = {}
                for line in dd.get_text("\n", strip=True).split("\n"):
                    line = line.strip()
                    if ":" in line:
                        k, v = line.split(":", 1)
                        stats[k.strip()] = v.strip()
                meta["stats"] = stats
            elif key == "Summary":
                meta[key] = value
            else:
                meta[key] = [v.strip() for v in value.split(",") if v.strip()]
    return meta


def work_id(preface: Tag) -> str | None:
    for a in preface.find_all("a", href=True):
        m = re.search(r"/works/(\d+)", a["href"])
        if m:
            return m.group(1)
    return None


def slugify(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return slug or "untitled"


def find_chapters(soup: BeautifulSoup) -> list[dict]:
    container = soup.find(id="chapters")
    if not container:
        body = soup.find("body")
        return [{"title": None, "body": chapter_body(body), "extras": []}] if body else []
    chapters = []
    pending_title = None
    for div in container.find_all("div", recursive=False):
        classes = div.get("class") or []
        if "meta" in classes:
            heading = div.find(["h1", "h2", "h3"])
            pending_title = heading.get_text(" ", strip=True) if heading else None
            # chapter summary / notes live in this block, keep them as quoted matter
            extras = []
            for extra in div.find_all("div", class_=re.compile("summary|notes|end")):
                text = chapter_body(extra)
                if text:
                    extras.append(text)
            if extras:
                pending_title = (pending_title, extras)
        elif "userstuff" in classes:
            title = pending_title[0] if isinstance(pending_title, tuple) else pending_title
            extras = pending_title[1] if isinstance(pending_title, tuple) else []
            chapters.append({"title": title, "body": chapter_body(div), "extras": extras})
            pending_title = None
    return chapters


def build_meta(soup: BeautifulSoup, html_path: Path) -> dict:
    preface = soup.find(id="preface")
    meta = parse_meta(preface) if preface else {}
    wid = work_id(preface) if preface else None
    title = None
    author = None
    if preface:
        h1 = preface.find("h1")
        title = h1.get_text(" ", strip=True) if h1 else None
        byline = preface.find(class_="byline")
        if byline:
            author = byline.get_text(" ", strip=True).removeprefix("by ").strip()
    summary = None
    if preface:
        summary_p = preface.find("blockquote")
        if summary_p:
            summary = chapter_body(summary_p)
    return {
        "work_id": wid,
        "url": f"https://archiveofourown.org/works/{wid}" if wid else None,
        "title": title,
        "author": author,
        "rating": meta.get("Rating"),
        "warnings": meta.get("Archive Warning"),
        "categories": meta.get("Category"),
        "fandoms": meta.get("Fandom"),
        "relationships": meta.get("Relationship"),
        "characters": meta.get("Characters"),
        "tags": meta.get("Additional Tags"),
        "language": ", ".join(meta.get("Language") or []) or "English",
        "series": ", ".join(meta.get("Series") or []),
        "stats": meta.get("stats"),
        "summary": summary,
        "source_file": str(html_path),
        "converted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def yaml_quote(value) -> str:
    if isinstance(value, list):
        value = ", ".join(value)
    value = str(value)
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def convert(html_path: Path, root: Path, force: bool = False) -> Path:
    soup = BeautifulSoup(html_path.read_text(encoding="utf-8"), "html.parser")
    meta = build_meta(soup, html_path)
    chapters = [c for c in find_chapters(soup) if c["body"].strip()]
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
        parts = [f"# {heading}"]
        for extra in ch["extras"]:
            parts.append("\n".join("> " + line if line else ">" for line in extra.split("\n")))
        parts.append(ch["body"])
        text = "\n\n".join(p for p in parts if p.strip())
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
    ap.add_argument("html", type=Path, help="AO3 downloaded HTML file")
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
