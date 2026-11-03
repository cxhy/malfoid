#!/usr/bin/env python3
"""Convert a chapter-numbered PDF into Markdown for the translation pipeline.

The AO3 HTML export of a work can lag behind the author's complete PDF (e.g.
Book VI only exists in full as a Google Docs PDF export). This script extracts
layout-preserving text with `pdftotext -layout`, rebuilds paragraphs from
indentation and blank lines, splits on sequential chapter headings ("1. Title",
"2. Title", ...), and writes the same layout `ao3_convert.py` produces:

    works/<work-id>-<slug>/
        meta.json
        source/original.pdf
        source/work.md
        source/chapters/ch-NN.md

Usage:
    .venv/bin/python tools/pdf_convert.py "Green_and_Silver/Book_VI.pdf" \
        --meta-from Green_and_Silver/Green_and_Silver_Book_VI.html \
        --work-id 95404276 --force
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from bs4 import BeautifulSoup

HEADING_RE = re.compile(r"^\s{0,20}(\d{1,2})\.\u200b?\s*(.+?)\s*$")
PARAGRAPH_INDENT = 5


def extract_text(pdf: Path) -> str:
    """Extract text preserving column layout; pdftotext is the reliable choice."""
    if shutil.which("pdftotext"):
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as tmp:
            out = Path(tmp.name)
        try:
            subprocess.run(["pdftotext", "-layout", str(pdf), str(out)], check=True)
            return out.read_text(encoding="utf-8")
        finally:
            out.unlink(missing_ok=True)
    try:
        from pypdf import PdfReader
    except ImportError:
        sys.exit("pdftotext not found and pypdf is not installed; install one of them.")
    reader = PdfReader(str(pdf))
    return "\n\f".join(page.extract_text() or "" for page in reader.pages)


def parse_chapters(text: str) -> list[dict]:
    """Split extracted text into chapters; rebuild paragraphs by indentation."""
    lines: list[str] = []
    for page in text.split("\f"):
        plines = page.split("\n")
        # pdftotext terminates each page with a newline; that empty element is a
        # page boundary, not a paragraph break, so drop exactly one.
        if plines and plines[-1] == "":
            plines.pop()
        lines.extend(plines)

    chapters: list[dict] = []
    current: dict | None = None
    paragraph: list[str] = []

    def flush() -> None:
        nonlocal paragraph
        if current is not None and paragraph:
            body = " ".join(paragraph)
            body = re.sub(r"\s+", " ", body).strip()
            if body:
                current["paragraphs"].append(body)
        paragraph = []

    i = 0
    expected = 1
    while i < len(lines):
        raw = lines[i]
        line = raw.rstrip()
        if not line.strip():
            flush()
            i += 1
            continue

        m = HEADING_RE.match(line)
        if m and int(m.group(1)) == expected and len(line.strip()) < 100:
            flush()
            title = m.group(2).strip()
            # A heading may wrap onto the next line at column 0.
            j = i + 1
            while j < len(lines):
                nxt = lines[j].rstrip()
                if not nxt.strip():
                    break
                indent = len(nxt) - len(nxt.lstrip())
                if indent != 0 or len(nxt.strip()) > 60 or title.endswith((".", "!", "?", "…")):
                    break
                title += " " + nxt.strip()
                j += 1
            current = {"index": expected, "title": title, "paragraphs": []}
            chapters.append(current)
            expected += 1
            i = j
            continue

        indent = len(line) - len(line.lstrip())
        stripped = line.strip()
        if indent >= PARAGRAPH_INDENT:
            flush()
            paragraph = [stripped]
        elif current is not None:
            if not paragraph:
                paragraph = [stripped]
            else:
                paragraph.append(stripped)
        i += 1

    flush()
    return [c for c in chapters if c["paragraphs"]]


def parse_meta_from_html(html: Path) -> dict:
    soup = BeautifulSoup(html.read_text(encoding="utf-8"), "html.parser")
    preface = soup.find(id="preface")
    meta: dict = {}
    if not preface:
        return meta
    for dl in preface.find_all("dl"):
        for dt in dl.find_all("dt"):
            key = dt.get_text(" ", strip=True).rstrip(":").strip()
            dd = dt.find_next_sibling("dd")
            if not dd:
                continue
            if key == "Stats":
                continue
            value = dd.get_text(" ", strip=True)
            meta[key] = [v.strip() for v in value.split(",") if v.strip()]
    h1 = preface.find("h1")
    byline = preface.find(class_="byline")
    return {
        "title": h1.get_text(" ", strip=True) if h1 else None,
        "author": byline.get_text(" ", strip=True).removeprefix("by ").strip() if byline else None,
        "rating": meta.get("Rating"),
        "warnings": meta.get("Archive Warning"),
        "categories": meta.get("Category"),
        "fandoms": meta.get("Fandom"),
        "relationships": meta.get("Relationship"),
        "characters": meta.get("Characters"),
        "tags": meta.get("Additional Tags"),
        "language": ", ".join(meta.get("Language") or []) or "English",
        "series": ", ".join(meta.get("Series") or []),
    }


def slugify(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return slug or "untitled"


def yaml_quote(value) -> str:
    if isinstance(value, list):
        value = ", ".join(value)
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def pdf_pages(pdf: Path) -> int:
    if shutil.which("pdfinfo"):
        out = subprocess.run(
            ["pdfinfo", str(pdf)], capture_output=True, text=True, check=True
        ).stdout
        m = re.search(r"^Pages:\s+(\d+)", out, re.M)
        if m:
            return int(m.group(1))
    return 0


def convert(
    pdf: Path, root: Path, meta_from: Path | None, work_id: str | None, force: bool = False
) -> Path:
    chapters = parse_chapters(extract_text(pdf))
    if not chapters:
        sys.exit(f"No numbered chapters found in {pdf}")

    meta = parse_meta_from_html(meta_from) if meta_from else {}
    title = meta.get("title") or pdf.stem
    wid = work_id or meta.get("work_id") or "unknown"
    slug = slugify(title)
    work_dir = root / f"{wid}-{slug}"
    if work_dir.exists() and not force:
        sys.exit(f"{work_dir} already exists; rerun with --force to overwrite")

    source = work_dir / "source"
    chapters_dir = source / "chapters"
    chapters_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(pdf, source / "original.pdf")

    merged_parts: list[str] = []
    records: list[dict] = []
    for ch in chapters:
        heading = f"Chapter {ch['index']}: {ch['title']}"
        body = "\n\n".join(ch["paragraphs"])
        text = f"# {heading}\n\n{body}"
        (chapters_dir / f"ch-{ch['index']:02d}.md").write_text(text + "\n", encoding="utf-8")
        merged_parts.append(text)
        records.append(
            {
                "index": ch["index"],
                "title": heading,
                "words": len(re.findall(r"[A-Za-z']+", body)),
                "chars": len(text),
            }
        )

    front = [
        "---",
        f"title: {yaml_quote(title)}",
        f"author: {yaml_quote(meta.get('author') or '')}",
        f"source: {yaml_quote(f'https://archiveofourown.org/works/{wid}')}",
        f"language: {yaml_quote(meta.get('language') or 'en')}",
        "---",
        "",
    ]
    (source / "work.md").write_text(
        "\n".join(front) + "\n\n".join(merged_parts) + "\n", encoding="utf-8"
    )

    meta.update(
        {
            "work_id": wid,
            "url": f"https://archiveofourown.org/works/{wid}",
            "title": title,
            "source_file": str(pdf),
            "meta_source": str(meta_from) if meta_from else None,
            "converted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "pdf_pages": pdf_pages(pdf),
            "stats": {
                "Pages": pdf_pages(pdf),
                "Words": sum(r["words"] for r in records),
                "Chapters": f"{len(records)}/{len(records)}",
            },
            "chapters": records,
            "chapter_count": len(records),
            "word_count": sum(r["words"] for r in records),
        }
    )
    (work_dir / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return work_dir


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("pdf", type=Path, help="PDF with numbered chapter headings")
    ap.add_argument("--root", type=Path, default=Path("works"), help="output root (default: works)")
    ap.add_argument("--meta-from", type=Path, help="AO3 HTML export to copy metadata from")
    ap.add_argument("--work-id", help="AO3 work id (overrides metadata)")
    ap.add_argument("--force", action="store_true", help="overwrite an existing work directory")
    args = ap.parse_args()
    if not args.pdf.is_file():
        sys.exit(f"Not found: {args.pdf}")
    work_dir = convert(args.pdf, args.root, args.meta_from, args.work_id, args.force)
    meta = json.loads((work_dir / "meta.json").read_text(encoding="utf-8"))
    print(f"{work_dir}")
    print(f"  title:    {meta['title']}")
    print(f"  author:   {meta['author']}")
    print(
        f"  chapters: {meta['chapter_count']}  words: {meta['word_count']}  pages: {meta['pdf_pages']}"
    )
    print(f"  md:       {work_dir}/source/work.md")


if __name__ == "__main__":
    main()
