#!/usr/bin/env python3
"""Convert a malfoid.lol EPUB into a v2-ingestable Markdown source bundle.

The reading edition at malfoid.lol ships one EPUB per book. Each EPUB has a
standard OPF spine, one XHTML file per chapter (and an optional interlude), and a
navigation document that carries the author's chapter titles. This converter
reads that structure directly, keeps the author's chapter numbers, renders
paragraphs and inline emphasis, and writes a source bundle:

    <output>/
        work.md              serialized source with ``Ch N: Title`` markers
        meta.json            book metadata + chapter records
        original/<file>.epub untouched input copy

The Markdown uses serialized chapter markers rather than ``#`` headings so that
``ingest_book_v2.py`` keeps the original numbering (including interludes such as
``Ch 20.5``). No network access is performed here; downloading the EPUB is a
separate, explicit step.

Usage:
    .venv/bin/python tools/epub_convert.py book.epub --output /tmp/book-01
    .venv/bin/python tools/epub_convert.py book.epub --output /tmp/book-03 \
        --work-id wattpad-418454516 --source-url https://malfoid.lol/the-imprisoned-girl/
"""

from __future__ import annotations

import argparse
import hashlib
import json
import posixpath
import re
import shutil
import sys
import warnings
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from ao3_convert import inline
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

CHAPTER_NUM_RE = re.compile(r"\bchapter\s+(\d+(?:\.\d+)?)", re.IGNORECASE)
CHAPTER_PREFIX_RE = re.compile(
    r"^\s*chapter\s+\d+(?:\.\d+)?\s*(?:[:.\-–—+]\s*)?", re.IGNORECASE
)
INTERLUDE_RE = re.compile(r"\binterlude\b", re.IGNORECASE)


class EpubError(ValueError):
    """Raised when an EPUB does not expose the expected spine or chapter files."""


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "untitled"


def _resolve(base: str, href: str) -> str:
    href = href.split("#", 1)[0]
    return posixpath.normpath(posixpath.join(base, href))


def _opf_path(archive: zipfile.ZipFile) -> str:
    try:
        container = archive.read("META-INF/container.xml").decode("utf-8")
    except KeyError as exc:
        raise EpubError("EPUB is missing META-INF/container.xml") from exc
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", XMLParsedAsHTMLWarning)
        soup = BeautifulSoup(container, "html.parser")
    rootfile = soup.find("rootfile")
    if rootfile is None or not rootfile.get("full-path"):
        raise EpubError("EPUB container has no rootfile")
    return str(rootfile["full-path"])


def _package(archive: zipfile.ZipFile, opf_path: str) -> dict:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", XMLParsedAsHTMLWarning)
        soup = BeautifulSoup(archive.read(opf_path).decode("utf-8"), "html.parser")
    base = posixpath.dirname(opf_path)

    manifest: dict[str, dict] = {}
    for item in soup.find_all("item"):
        item_id = item.get("id")
        href = item.get("href")
        if item_id and href:
            manifest[item_id] = {
                "href": _resolve(base, href),
                "properties": item.get("properties") or "",
                "media_type": item.get("media-type") or "",
            }

    spine = [
        manifest[item.get("idref")]
        for item in soup.find_all("itemref")
        if item.get("idref") in manifest
    ]

    def text_of(tag: str) -> str:
        node = soup.find(tag)
        return node.get_text(" ", strip=True) if node else ""

    # HTML parsers treat <meta> as void, so read the text-bearing EPUB
    # metadata elements directly from the package document.
    raw = archive.read(opf_path).decode("utf-8")
    status_match = re.search(
        r'<meta[^>]*property="malfoid:status"[^>]*>(.*?)</meta>', raw, re.DOTALL
    )
    series_match = re.search(
        r'<meta[^>]*property="belongs-to-collection"[^>]*>(.*?)</meta>', raw, re.DOTALL
    )
    status = status_match.group(1).strip() if status_match else ""
    series = series_match.group(1).strip() if series_match else ""

    return {
        "title": text_of("dc:title"),
        "author": text_of("dc:creator"),
        "source_url": text_of("dc:source"),
        "description": text_of("dc:description"),
        "series": series,
        "status": status,
        "spine": spine,
        "manifest": manifest,
    }


def _nav_titles(archive: zipfile.ZipFile, package: dict) -> dict[str, str]:
    nav_href = ""
    for item in package["manifest"].values():
        if "nav" in item["properties"].split():
            nav_href = item["href"]
            break
    if not nav_href:
        return {}
    try:
        soup = BeautifulSoup(archive.read(nav_href).decode("utf-8"), "html.parser")
    except KeyError:
        return {}
    nav = soup.find("nav", attrs={"epub:type": "toc"}) or soup.find("nav")
    if nav is None:
        return {}
    base = posixpath.dirname(nav_href)
    titles: dict[str, str] = {}
    for link in nav.find_all("a"):
        href = link.get("href")
        if not href:
            continue
        title = link.get_text(" ", strip=True)
        if title:
            titles[_resolve(base, href)] = title
    return titles


def _chapter_heading(archive: zipfile.ZipFile, href: str, nav_title: str) -> dict:
    soup = BeautifulSoup(archive.read(href).decode("utf-8"), "html.parser")
    chapter_num = soup.find(class_="chapter-num")
    chapter_num_text = chapter_num.get_text(" ", strip=True) if chapter_num else ""
    h1 = soup.find("h1")
    body_title = h1.get_text(" ", strip=True) if h1 else ""
    document_title = ""
    title_tag = soup.find("title")
    if title_tag:
        document_title = title_tag.get_text(" ", strip=True)

    prose = soup.find(class_="prose") or soup.find("section") or soup.find("body")
    paragraphs: list[str] = []
    if prose is not None:
        for paragraph in prose.find_all("p"):
            classes = paragraph.get("class") or []
            if "chapter-num" in classes:
                continue
            text = inline(paragraph)
            text = re.sub(r"\s+", " ", text).strip()
            if text:
                paragraphs.append(text)
    return {
        "chapter_num": chapter_num_text,
        "nav_title": nav_title,
        "body_title": body_title,
        "document_title": document_title,
        "paragraphs": paragraphs,
    }


def _strip_chapter_prefix(title: str) -> str:
    return CHAPTER_PREFIX_RE.sub("", title).strip()


def _assign_number(
    heading: dict, last_number: float | None
) -> tuple[float, str, bool]:
    """Return (number, marker title, is_interlude) for one content file."""
    combined = f"{heading['chapter_num']} {heading['nav_title']} {heading['body_title']}"
    match = CHAPTER_NUM_RE.search(combined)
    interlude = bool(INTERLUDE_RE.search(combined))
    if match:
        number = float(match.group(1))
    elif interlude:
        if last_number is None:
            number = 0.5
        elif float(last_number).is_integer():
            number = float(last_number) + 0.5
        else:
            number = float(last_number) + 0.1
    else:
        number = 1.0 if last_number is None else float(last_number) + 1.0

    if interlude:
        title = heading["nav_title"] or heading["body_title"] or "Interlude"
        if not INTERLUDE_RE.search(title):
            title = f"Interlude: {title}"
        return number, title, True

    title = _strip_chapter_prefix(heading["nav_title"] or heading["body_title"])
    if not title:
        title = heading["document_title"] or f"Chapter {number:g}"
        title = _strip_chapter_prefix(title)
    return number, title, False


def _number_text(number: float) -> str:
    return f"{number:g}"


def read_epub(path: Path) -> dict:
    """Parse an EPUB into book metadata and ordered chapter records."""
    with zipfile.ZipFile(path) as archive:
        opf_path = _opf_path(archive)
        package = _package(archive, opf_path)
        nav_titles = _nav_titles(archive, package)

        content = []
        for item in package["spine"]:
            href = item["href"]
            title = nav_titles.get(href, "")
            lowered = href.lower()
            if any(part in lowered for part in ("cover", "title", "nav", "sources")):
                continue
            if title.lower().startswith("about this edition"):
                continue
            content.append((href, title))

        if not content:
            raise EpubError("EPUB spine has no chapter content")

        chapters = []
        last_number: float | None = None
        for href, nav_title in content:
            heading = _chapter_heading(archive, href, nav_title)
            if not heading["paragraphs"]:
                continue
            number, title, interlude = _assign_number(heading, last_number)
            marker = f"Ch {_number_text(number)}: {title}"
            chapters.append(
                {
                    "index": number,
                    "marker": marker,
                    "title": title,
                    "interlude": interlude,
                    "paragraphs": heading["paragraphs"],
                }
            )
            last_number = number

    if not chapters:
        raise EpubError("EPUB has no chapter paragraphs")

    return {
        "title": package["title"] or path.stem,
        "author": package["author"],
        "source_url": package["source_url"],
        "description": package["description"],
        "series": package["series"],
        "status": package["status"],
        "chapters": chapters,
    }


def render_work(chapters: list[dict]) -> str:
    blocks = []
    for chapter in chapters:
        body = "\n\n".join(chapter["paragraphs"])
        blocks.append(f"{chapter['marker']}\n\n{body}")
    return "\n\n".join(blocks) + "\n"


def convert(
    epub: Path,
    output: Path,
    *,
    work_id: str = "",
    title: str = "",
    author: str = "",
    series: str = "",
    source_url: str = "",
    force: bool = False,
) -> Path:
    epub = Path(epub)
    output = Path(output)
    if output.exists() and any(output.iterdir()) and not force:
        sys.exit(f"{output} already exists; rerun with --force to overwrite")
    output.mkdir(parents=True, exist_ok=True)

    data = read_epub(epub)
    resolved_title = title or data["title"]
    resolved_author = author or data["author"]
    resolved_series = series or data["series"]
    resolved_url = source_url or data["source_url"]
    resolved_id = work_id or f"epub-{slugify(resolved_title)}"

    work_md = render_work(data["chapters"])
    (output / "work.md").write_text(work_md, encoding="utf-8")

    original_dir = output / "original"
    original_dir.mkdir(exist_ok=True)
    shutil.copy2(epub, original_dir / epub.name)

    digest = hashlib.sha256(epub.read_bytes()).hexdigest()
    chapter_records = []
    for chapter in data["chapters"]:
        text = "\n\n".join(chapter["paragraphs"])
        number = chapter["index"]
        if isinstance(number, float) and number.is_integer():
            number = int(number)
        chapter_records.append(
            {
                "index": number,
                "title": chapter["marker"],
                "covered_chapters": [number],
                "interlude": chapter["interlude"],
                "paragraph_count": len(chapter["paragraphs"]),
                "words": len(re.findall(r"[A-Za-z']+", text)),
                "chars": len(text),
            }
        )
    meta = {
        "work_id": resolved_id,
        "url": resolved_url,
        "source_site": "malfoid.lol",
        "title": resolved_title,
        "author": resolved_author,
        "series": resolved_series,
        "source_file": str(epub),
        "source_epub": epub.name,
        "source_sha256": digest,
        "source_status": data["status"],
        "source_complete": data["status"].lower() == "complete",
        "summary": data["description"],
        "converted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "chapters": chapter_records,
        "chapter_count": len(chapter_records),
        "word_count": sum(record["words"] for record in chapter_records),
    }
    (output / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return output


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("epub", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--work-id", default="")
    parser.add_argument("--title", default="")
    parser.add_argument("--author", default="")
    parser.add_argument("--series", default="")
    parser.add_argument("--source-url", default="")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    convert(
        args.epub,
        args.output,
        work_id=args.work_id,
        title=args.title,
        author=args.author,
        series=args.series,
        source_url=args.source_url,
        force=args.force,
    )
    print(f"wrote {args.output / 'work.md'}")


if __name__ == "__main__":
    main()
