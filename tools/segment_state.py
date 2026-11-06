"""Source ingestion, segment state and Markdown rendering (pipeline v2).

A source file becomes chapters of segments. Every heading starts a chapter; blank
lines separate paragraphs; single internal newlines stay inside a paragraph. Chapter
state lives in one JSON document per chapter; Markdown is rendered from that state.

Text segments larger than the per-segment budget are split at sentence boundaries.
Each part keeps its own source hash; all parts carry the logical source hash and
logical index, and continuation parts are marked ``cont`` and rendered back into a
single paragraph.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from token_budget import estimate_tokens

CHAPTERS_DIR = "chapters"
DEFAULT_SEGMENT_TOKEN_BUDGET = 800
_MD_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
_WATTPAD_MARKER_RE = re.compile(r"^\s*===\s*(.*?)\s*===\s*$")
_SENTENCE_END_RE = re.compile(r"""[.!?]+["'”’)\]]*(?=\s|$)""")
_WORD_BEFORE_RE = re.compile(r"([A-Za-z]+)$")
_ABBREVIATIONS = frozenset({"mr", "mrs", "ms", "dr", "prof", "sr", "jr", "st", "vs", "etc"})


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


def _is_sentence_end(text: str, match: re.Match[str]) -> bool:
    """Reject common abbreviations and initials as false sentence endings."""
    if match.group(0)[0] != ".":
        return True
    word = _WORD_BEFORE_RE.search(text[: match.start()])
    if word:
        token = word.group(1)
        if token.lower() in _ABBREVIATIONS or (len(token) == 1 and token.isupper()):
            return False
    return True


def _sentence_units(text: str) -> list[str]:
    """Split text into sentence units whose concatenation equals the input."""
    units: list[str] = []
    start = 0
    for match in _SENTENCE_END_RE.finditer(text):
        if not _is_sentence_end(text, match):
            continue
        end = match.end()
        while end < len(text) and text[end].isspace():
            end += 1
        if end == match.end():
            continue
        units.append(text[start:end])
        start = end
    if start < len(text):
        units.append(text[start:])
    return units or [text]


def _split_text(text: str, max_segment_tokens: int) -> list[str]:
    """Pack sentence units greedily; an oversized single sentence stays whole."""
    parts: list[str] = []
    current = ""
    for unit in _sentence_units(text):
        if current and estimate_tokens(current + unit) > max_segment_tokens:
            parts.append(current)
            current = unit
        else:
            current += unit
    if current:
        parts.append(current)
    return parts or [text]


def split_long_segment(
    segment: dict, max_segment_tokens: int = DEFAULT_SEGMENT_TOKEN_BUDGET
) -> list[dict]:
    """Split one oversized text segment at sentence boundaries.

    Heading segments, segments within the budget and existing continuation parts
    are returned unchanged. A single sentence longer than the budget has no
    sentence boundary to split at, so it stays whole. Each part has its own source
    hash; all parts carry ``logical_source_hash`` and ``logical_index``, and
    continuation parts are marked ``cont``.
    """
    text = segment.get("source") or ""
    if segment.get("kind") != "text" or segment.get("cont"):
        return [segment]
    if estimate_tokens(text) <= max_segment_tokens:
        return [segment]
    parts_text = _split_text(text, max_segment_tokens)
    if len(parts_text) <= 1:
        return [segment]

    logical_hash = (
        segment.get("logical_source_hash") or segment.get("source_hash") or source_hash(text)
    )
    logical_index = int(segment.get("logical_index", segment.get("index", 0)))
    parts: list[dict] = []
    for number, part_text in enumerate(parts_text):
        part = {
            "index": int(segment.get("index", 0)) + number,
            "kind": "text",
            "source": part_text,
            "source_hash": source_hash(part_text),
            "target": segment.get("target") if number == 0 else None,
            "logical_index": logical_index,
            "logical_source_hash": logical_hash,
            "part_number": number,
        }
        if number > 0:
            part["cont"] = True
        parts.append(part)
    return parts


def split_segments(
    segments: list[dict], max_segment_tokens: int = DEFAULT_SEGMENT_TOKEN_BUDGET
) -> list[dict]:
    """Apply the per-segment budget, preserving unsplit segment shape."""
    result: list[dict] = []
    for segment in segments:
        parts = split_long_segment(segment, max_segment_tokens)
        if len(parts) == 1:
            part = dict(parts[0])
            part["index"] = len(result)
            result.append(part)
            continue
        for part in parts:
            part = dict(part)
            part["index"] = len(result)
            result.append(part)
    return result


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


def parse_source(
    text: str,
    fallback_title: str = "untitled",
    max_segment_tokens: int | None = DEFAULT_SEGMENT_TOKEN_BUDGET,
) -> list[dict]:
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
    if max_segment_tokens is not None:
        for chapter in chapters:
            chapter["segments"] = split_segments(chapter["segments"], max_segment_tokens)
    return chapters


def read_source(
    path: Path, max_segment_tokens: int | None = DEFAULT_SEGMENT_TOKEN_BUDGET
) -> list[dict]:
    return parse_source(path.read_text(encoding="utf-8"), path.stem, max_segment_tokens)


def chapter_path(book_dir: Path, number: int) -> Path:
    return book_dir / "source" / CHAPTERS_DIR / f"ch-{number:02d}.json"


def save_chapter(path: Path, chapter: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(chapter, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def load_chapter(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def logical_blocks(chapter: dict) -> list[dict]:
    """Collapse continuation parts into logical blocks (heading or text)."""
    blocks: list[dict] = []
    for segment in chapter.get("segments") or []:
        text = segment.get("source") or ""
        if segment.get("cont") and blocks and blocks[-1]["kind"] == "text":
            blocks[-1]["text"] += text
        else:
            blocks.append(
                {
                    "kind": segment.get("kind"),
                    "level": segment.get("level"),
                    "text": text,
                }
            )
    return blocks


def render_markdown(chapter: dict) -> str:
    """Render source blocks, merging continuation parts into one paragraph."""
    parts: list[str] = []
    for block in logical_blocks(chapter):
        if block["kind"] == "heading":
            level = int(block.get("level") or chapter.get("heading_level") or 1)
            parts.append(f"{'#' * level} {block['text']}")
        else:
            parts.append(block["text"])
    return "\n\n".join(parts)


def chapter_text(chapter: dict) -> str:
    """Join logical blocks into plain text for digests, hashes and meta records."""
    return "\n\n".join(block["text"] for block in logical_blocks(chapter))


def verify_round_trip(
    chapter: dict, max_segment_tokens: int | None = DEFAULT_SEGMENT_TOKEN_BUDGET
) -> bool:
    """Render one chapter and parse it back; compare logical structure and text."""
    rendered = render_markdown(chapter)
    parsed = parse_source(rendered, chapter.get("title") or "untitled", max_segment_tokens)
    if len(parsed) != 1:
        return False
    got = parsed[0]
    if got.get("title") != chapter.get("title"):
        return False
    left = [(b["kind"], b["level"], b["text"]) for b in logical_blocks(chapter)]
    right = [(b["kind"], b["level"], b["text"]) for b in logical_blocks(got)]
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
    text = chapter_text(chapter)
    return {
        "index": int(chapter.get("chapter") or 0),
        "title": chapter.get("title") or "",
        "words": len(re.findall(r"[A-Za-z']+", text)),
        "chars": len(text),
    }


def ingest_source(
    book_dir: Path,
    source_path: Path,
    max_segment_tokens: int | None = DEFAULT_SEGMENT_TOKEN_BUDGET,
) -> list[Path]:
    """Write chapter state, regenerate source/work.md, and refresh book metadata."""
    chapters = read_source(source_path, max_segment_tokens)
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
