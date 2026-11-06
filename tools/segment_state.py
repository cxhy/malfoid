"""Source ingestion, segment state and Markdown rendering (pipeline v2, ticket 02).

A source file becomes chapters of segments. Every heading starts a chapter; blank
lines separate paragraphs; single internal newlines stay inside a paragraph. Chapter
state lives in one JSON document per chapter; Markdown is rendered from that state.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

CHAPTERS_DIR = "chapters"
_MD_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
_WATTPAD_MARKER_RE = re.compile(r"^\s*===\s*(.*?)\s*===\s*$")


def source_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _parse_heading(line: str) -> tuple[int, str] | None:
    m = _MD_HEADING_RE.match(line)
    if m:
        return len(m.group(1)), m.group(2).strip()
    m = _WATTPAD_MARKER_RE.match(line)
    if m:
        return 1, m.group(1).strip()
    return None


def _segment(index: int, kind: str, text: str, level: int | None = None) -> dict:
    segment = {
        "index": index,
        "kind": kind,
        "source": text,
        "source_hash": source_hash(text),
        "target": None,
    }
    if level is not None:
        segment["level"] = level
    return segment


def _new_chapter(title: str, level: int | None, segments: list[dict] | None = None) -> dict:
    return {
        "chapter": 0,
        "title": title,
        "heading_level": level,
        "segments": segments or [],
    }


def parse_source(text: str, fallback_title: str = "untitled") -> list[dict]:
    """Parse plain text or Markdown into chapters of segments."""
    lines = text.splitlines()
    chapters: list[dict] = []
    current: dict | None = None
    para: list[str] = []

    def flush_para() -> None:
        nonlocal para
        if current is None:
            para = []
            return
        if para:
            body = "\n".join(para).strip()
            if body:
                current["segments"].append(_segment(len(current["segments"]), "text", body))
            para = []

    def start_chapter(title: str, level: int | None, segments: list[dict] | None = None) -> None:
        nonlocal current
        flush_para()
        current = _new_chapter(title, level, segments)
        current["chapter"] = len(chapters) + 1
        chapters.append(current)

    for line in lines:
        found = _parse_heading(line)
        if found:
            level, title = found
            start_chapter(title, level, [_segment(0, "heading", title, level)])
            continue
        if current is None:
            start_chapter(fallback_title, None)
        if line.strip() == "":
            flush_para()
        else:
            para.append(line)
    flush_para()
    if not chapters:
        start_chapter(fallback_title, None)
    return chapters


def read_source(path: Path) -> list[dict]:
    return parse_source(path.read_text(encoding="utf-8"), path.stem)


def chapter_path(book_dir: Path, number: int) -> Path:
    return book_dir / "source" / CHAPTERS_DIR / f"ch-{number:02d}.json"


def save_chapter(path: Path, chapter: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(chapter, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def load_chapter(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def render_markdown(chapter: dict) -> str:
    parts: list[str] = []
    for segment in chapter.get("segments") or []:
        text = segment.get("source") or ""
        if segment.get("kind") == "heading":
            level = int(segment.get("level") or chapter.get("heading_level") or 1)
            parts.append(f"{'#' * level} {text}")
        else:
            parts.append(text)
    return "\n\n".join(parts)


def verify_round_trip(chapter: dict) -> bool:
    """Render one chapter and parse it back; compare structure and segment text."""
    rendered = render_markdown(chapter)
    parsed = parse_source(rendered, chapter.get("title") or "untitled")
    if len(parsed) != 1:
        return False
    got = parsed[0]
    if got.get("title") != chapter.get("title"):
        return False
    left = [(s.get("kind"), s.get("level"), s.get("source")) for s in chapter.get("segments") or []]
    right = [(s.get("kind"), s.get("level"), s.get("source")) for s in got.get("segments") or []]
    return left == right


def _chapter_sort_key(path: Path) -> int:
    m = re.search(r"(\d+)$", path.stem)
    return int(m.group(1)) if m else 0


def render_book(book_dir: Path) -> str:
    chapters_dir = book_dir / "source" / CHAPTERS_DIR
    paths = sorted(chapters_dir.glob("ch-*.json"), key=_chapter_sort_key)
    chapters = [load_chapter(path) for path in paths]
    return "\n\n".join(render_markdown(chapter) for chapter in chapters)


def _chapter_record(chapter: dict) -> dict:
    text = "\n\n".join(segment.get("source") or "" for segment in chapter.get("segments") or [])
    return {
        "index": int(chapter.get("chapter") or 0),
        "title": chapter.get("title") or "",
        "words": len(re.findall(r"[A-Za-z']+", text)),
        "chars": len(text),
    }


def ingest_source(book_dir: Path, source_path: Path) -> list[Path]:
    """Write chapter state, regenerate source/work.md, and refresh book metadata."""
    chapters = read_source(source_path)
    chapters_dir = book_dir / "source" / CHAPTERS_DIR
    chapters_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for chapter in chapters:
        path = chapter_path(book_dir, int(chapter["chapter"]))
        save_chapter(path, chapter)
        paths.append(path)

    work_md = "\n\n".join(render_markdown(chapter) for chapter in chapters) + "\n"
    (book_dir / "source" / "work.md").write_text(work_md, encoding="utf-8")

    meta_path = book_dir / "meta.json"
    if meta_path.is_file():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta["chapters"] = [_chapter_record(chapter) for chapter in chapters]
        meta["chapter_count"] = len(chapters)
        meta["word_count"] = sum(record["words"] for record in meta["chapters"])
        source_files = list(meta.get("source_files") or [])
        name = str(source_path)
        if name not in source_files:
            source_files.append(name)
        meta["source_files"] = source_files
        meta.setdefault("source_file", name)
        meta_path.write_text(
            json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    return paths
