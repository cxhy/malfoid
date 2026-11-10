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
from datetime import datetime, timezone
from pathlib import Path

from token_budget import estimate_tokens

CHAPTERS_DIR = "chapters"
DEFAULT_SEGMENT_TOKEN_BUDGET = 800
_MD_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
_WATTPAD_MARKER_RE = re.compile(r"^\s*===\s*(.*?)\s*===\s*$")
_CHAPTER_MARKER_RE = re.compile(
    r"^\s*(?:ch(?:apter)?s?)\s*(\d+(?:\.\d+)?)(.*)$",
    re.IGNORECASE,
)
_MARKER_TITLE_PREFIXES = (":", ".", "-", "–", "—", "+")
_PARENTHETICAL_TITLE_RE = re.compile(r"^\([^)]*\)$")
_CHAPTER_RANGE_RE = re.compile(
    r"(?:ch(?:apter)?s?)\s*(\d+(?:\.\d+)?)(?:\s*[-–—]\s*(\d+(?:\.\d+)?))?",
    re.IGNORECASE,
)
_BOOK_LABEL_RE = re.compile(r"^\s*book\s+\d+\s*$", re.IGNORECASE)
_SENTENCE_END_RE = re.compile(r"""[.!?]+["'”’)\]]*(?=\s|$)""")
_WORD_BEFORE_RE = re.compile(r"([A-Za-z]+)$")
_ABBREVIATIONS = frozenset({"mr", "mrs", "ms", "dr", "prof", "sr", "jr", "st", "vs", "etc"})


def source_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def chapter_number(value: int | float | str) -> int | float:
    """Normalize a chapter number, keeping sub-chapters as decimals (23.5)."""
    number = float(value)
    return int(number) if number.is_integer() else number


def chapter_slug(value: int | float | str) -> str:
    """Filesystem-safe chapter key: ``05`` for integers, ``23.5`` for decimals."""
    number = chapter_number(value)
    if isinstance(number, int):
        return f"{number:02d}"
    return f"{number:g}"


def _parse_heading(line: str) -> tuple[int, str] | None:
    m = _MD_HEADING_RE.match(line)
    if m:
        return len(m.group(1)), m.group(2).strip()
    m = _WATTPAD_MARKER_RE.match(line)
    if m:
        return 1, m.group(1).strip()
    return None


def _parse_chapter_marker(line: str) -> tuple[int | float, str] | None:
    """Recognise serialized markers such as ``Ch 6``, ``Ch 23.5`` or ``Ch 9 (Combination)``.

    A marker is a short chapter label: after the number comes either nothing, a
    title separator (``:``, ``.``, ``-``, ``+``) or a parenthetical tag. Prose
    lines that merely start with ``Ch 30 ...`` are not chapter markers.
    """
    m = _CHAPTER_MARKER_RE.match(line)
    if not m:
        return None
    rest = m.group(2).strip()
    if rest:
        if rest[0] in _MARKER_TITLE_PREFIXES:
            pass
        elif not _PARENTHETICAL_TITLE_RE.match(rest):
            return None
    return chapter_number(m.group(1)), line.strip()


def _chapter_range_start(text: str) -> int | float | None:
    """Return the first chapter number from a serialized segment title."""
    m = _CHAPTER_RANGE_RE.search(text)
    return chapter_number(m.group(1)) if m else None


def _shift_marker_title(title: str, number: int | float) -> str:
    """Renumber a bare chapter marker while leaving custom titles untouched."""
    m = re.fullmatch(r"\s*(Ch|Chapter)\s*\d+(?:\.\d+)?\s*", title, re.IGNORECASE)
    if not m:
        return title
    prefix = m.group(1)
    if prefix.islower():
        prefix = prefix.capitalize()
    return f"{prefix} {number:g}" if isinstance(number, float) else f"{prefix} {number}"


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


def chapter_path(book_dir: Path, number: int | float) -> Path:
    return book_dir / "source" / CHAPTERS_DIR / f"ch-{chapter_slug(number)}.json"


def save_chapter(path: Path, chapter: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(chapter, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def load_chapter(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _segment_text(segment: dict, translated: bool) -> str:
    if translated:
        target = segment.get("target")
        if isinstance(target, str) and target.strip():
            return target
    return segment.get("source") or ""


def logical_blocks(chapter: dict, translated: bool = False) -> list[dict]:
    """Collapse continuation parts into logical blocks (heading or text).

    Source mode is the default. ``translated=True`` renders targets instead and
    falls back to source text for a segment that has no target yet.
    """
    blocks: list[dict] = []
    for segment in chapter.get("segments") or []:
        text = _segment_text(segment, translated)
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


_CHAPTER_TITLE_RE = re.compile(
    r"^\s*(?:ch(?:apter)?s?)\s*(\d+(?:\.\d+)?(?:\s*[-–—+]\s*\d+(?:\.\d+)?)*)"
    r"\s*(?:\(.*\))?\s*$",
    re.IGNORECASE,
)
_CHINESE_DIGITS = "零一二三四五六七八九"


def _chinese_integer(number: int) -> str:
    if 0 <= number < 10:
        return _CHINESE_DIGITS[number]
    if 10 <= number < 20:
        return "十" + (_CHINESE_DIGITS[number % 10] if number % 10 else "")
    if 20 <= number < 100:
        return (
            _CHINESE_DIGITS[number // 10]
            + "十"
            + (_CHINESE_DIGITS[number % 10] if number % 10 else "")
        )
    return str(number)


def chinese_number(value: int | float) -> str:
    """Render a chapter number in Chinese, keeping decimals as ``点`` digits."""
    number = float(value)
    if number.is_integer():
        return _chinese_integer(int(number))
    whole, _, fraction = f"{number:g}".partition(".")
    return _chinese_integer(int(whole)) + "点" + "".join(
        _CHINESE_DIGITS[int(digit)] for digit in fraction
    )


def chapter_marker_numbers(title: str) -> list[int | float]:
    """Return the chapter numbers explicitly named in a serialized marker."""
    match = _CHAPTER_TITLE_RE.match(str(title or "").strip())
    if not match:
        return []
    return [chapter_number(value) for value in re.findall(r"\d+(?:\.\d+)?", match.group(1))]


def display_chapter_heading(chapter: dict) -> str | None:
    """Derive a deterministic heading from recorded or serialized chapter numbers.

    Returns ``None`` for custom titles that are not chapter markers, so callers
    keep the translated heading. Ranges collapse to first–last (``Ch18-19-20``
    becomes ``第十八至二十章``).
    """
    numbers: list[int | float] = []
    covered = chapter.get("covered_chapters")
    if isinstance(covered, list):
        for value in covered:
            try:
                numbers.append(chapter_number(value))
            except (TypeError, ValueError):
                continue
    if not numbers:
        numbers = chapter_marker_numbers(str(chapter.get("title") or ""))
    if not numbers:
        return None
    if len(numbers) == 1:
        return f"第{chinese_number(numbers[0])}章"
    return f"第{chinese_number(numbers[0])}至{chinese_number(numbers[-1])}章"


def render_markdown(
    chapter: dict, translated: bool = False, heading_text: str | None = None
) -> str:
    """Render logical blocks, merging continuation parts into one paragraph.

    ``heading_text`` replaces the first heading block only, so delivery can use a
    deterministic chapter heading instead of the model's heading translation.
    """
    parts: list[str] = []
    heading_used = False
    for block in logical_blocks(chapter, translated=translated):
        if block["kind"] == "heading":
            level = int(block.get("level") or chapter.get("heading_level") or 1)
            text = block["text"]
            if heading_text is not None and not heading_used:
                text = heading_text
                heading_used = True
            parts.append(f"{'#' * level} {text}")
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


def _chapter_sort_key(path: Path) -> float:
    m = re.search(r"(\d+(?:\.\d+)?)$", path.stem)
    return float(m.group(1)) if m else 0.0


def render_book(book_dir: Path, translated: bool = False) -> str:
    """Render a whole book in source mode, or targets with ``translated=True``.

    Translated delivery derives chapter headings deterministically from the
    chapter marker (number and range), so model heading drift cannot reach the
    reader-facing file. Custom non-marker titles keep their translated heading.
    """
    chapters_dir = book_dir / "source" / CHAPTERS_DIR
    paths = sorted(chapters_dir.glob("ch-*.json"), key=_chapter_sort_key)
    chapters = [load_chapter(path) for path in paths]
    parts = []
    for chapter in chapters:
        heading_text = display_chapter_heading(chapter) if translated else None
        parts.append(render_markdown(chapter, translated=translated, heading_text=heading_text))
    return "\n\n".join(parts)


def _chapter_record(chapter: dict) -> dict:
    text = chapter_text(chapter)
    blocks = logical_blocks(chapter)
    index = chapter_number(chapter.get("chapter") or 0)
    covered = chapter.get("covered_chapters")
    if not isinstance(covered, list) or not covered:
        covered = chapter_marker_numbers(str(chapter.get("title") or "")) or [index]
    return {
        "index": index,
        "title": chapter.get("title") or "",
        "covered_chapters": [chapter_number(value) for value in covered],
        "source_hash": source_hash(text),
        "paragraph_count": sum(1 for block in blocks if block["kind"] == "text"),
        "segment_count": len(chapter.get("segments") or []),
        "words": len(re.findall(r"[A-Za-z']+", text)),
        "chars": len(text),
    }


def _parse_serialized_source(
    text: str,
    fallback_title: str,
    max_segment_tokens: int | None,
    chapter_offset: int,
    chapter_start: int,
) -> list[dict]:
    """Parse a source that may contain several serialized chapter markers."""
    lines = text.splitlines()
    markers = [
        (index, marker)
        for index, line in enumerate(lines)
        if (marker := _parse_chapter_marker(line)) is not None
    ]
    if not markers:
        chapters = parse_source(text, fallback_title, max_segment_tokens)
        for position, chapter in enumerate(chapters):
            chapter["chapter"] = chapter_start + position
        return chapters

    segment_title = ""
    for line in lines[: markers[0][0]]:
        m = _WATTPAD_MARKER_RE.match(line)
        if m:
            segment_title = m.group(1).strip()
            break
    range_start = _chapter_range_start(segment_title)
    first_marker = markers[0][1][0]
    if range_start is not None:
        inferred_start = range_start + chapter_offset
    else:
        inferred_start = max(first_marker - 1 + chapter_offset, chapter_start)

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

    def start_chapter(number: int | float, title: str) -> None:
        nonlocal current
        flush_para()
        current = _new_chapter(title, 1, [_segment(0, "heading", title, 1)])
        current["chapter"] = number
        covered = chapter_marker_numbers(title)
        current["covered_chapters"] = covered or [number]
        chapters.append(current)

    for line in lines:
        marker = _parse_chapter_marker(line)
        if marker is not None:
            number, title = marker
            shifted = number + chapter_offset
            start_chapter(shifted, _shift_marker_title(title, shifted))
            continue
        if _WATTPAD_MARKER_RE.match(line):
            flush_para()
            continue
        if current is None:
            if line.strip() == "":
                continue
            # A bare "Book 2" line before the first chapter marker is a book
            # label, not the opening paragraph of an inferred chapter.
            if _BOOK_LABEL_RE.match(line):
                continue
            inferred_label = (
                f"Ch {inferred_start:g}" if isinstance(inferred_start, float) else f"Ch {inferred_start}"
            )
            start_chapter(inferred_start, inferred_label)
        if line.strip() == "":
            flush_para()
        else:
            para.append(line)
    flush_para()

    if max_segment_tokens is not None:
        for chapter in chapters:
            chapter["segments"] = split_segments(chapter["segments"], max_segment_tokens)
    return chapters


def ingest_sources(
    book_dir: Path,
    source_paths: list[Path],
    chapter_offset: int = 0,
    max_segment_tokens: int | None = DEFAULT_SEGMENT_TOKEN_BUDGET,
    complete: bool = False,
) -> list[Path]:
    """Ingest several serialized source files into one continuous book.

    Chapter markers in the source are authoritative and ``chapter_offset`` shifts
    them into the book's numbering. Sources without markers continue from the last
    ingested chapter. The completed book is marked with ``source_complete`` so a
    whole-book prescan can run.
    """
    paths = [Path(path) for path in source_paths]
    if not paths:
        raise ValueError("at least one source file is required")

    chapters: list[dict] = []
    next_start = int(chapter_offset) + 1
    for source_path in paths:
        text = source_path.read_text(encoding="utf-8")
        parsed = _parse_serialized_source(
            text,
            source_path.stem,
            max_segment_tokens,
            int(chapter_offset),
            next_start,
        )
        chapters.extend(parsed)
        if parsed:
            next_start = int(max(chapter_number(chapter["chapter"]) for chapter in parsed)) + 1

    numbers = [chapter_number(chapter["chapter"]) for chapter in chapters]
    if len(set(numbers)) != len(numbers):
        raise ValueError("duplicate chapter numbers across source files")
    chapters.sort(key=lambda chapter: chapter_number(chapter["chapter"]))

    chapters_dir = book_dir / "source" / CHAPTERS_DIR
    chapters_dir.mkdir(parents=True, exist_ok=True)
    paths_out: list[Path] = []
    for chapter in chapters:
        path = chapter_path(book_dir, chapter_number(chapter["chapter"]))
        save_chapter(path, chapter)
        paths_out.append(path)

    stale = [path for path in chapters_dir.glob("ch-*.json") if path not in paths_out]
    if stale:
        archive = (
            book_dir
            / "archive"
            / datetime.now(timezone.utc).isoformat(timespec="seconds")
            / "chapters"
        )
        archive.mkdir(parents=True, exist_ok=True)
        for path in stale:
            path.replace(archive / path.name)

    source_dir = book_dir / "source"
    source_dir.mkdir(parents=True, exist_ok=True)
    work_md = "\n\n".join(render_markdown(chapter) for chapter in chapters) + "\n"
    (source_dir / "work.md").write_text(work_md, encoding="utf-8")

    meta_path = book_dir / "meta.json"
    if meta_path.is_file():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta["chapters"] = [_chapter_record(chapter) for chapter in chapters]
        meta["chapter_count"] = len(chapters)
        meta["word_count"] = sum(record["words"] for record in meta["chapters"])
        meta["source_files"] = [str(path) for path in paths]
        meta.setdefault("source_file", str(paths[0]))
        meta["source_complete"] = bool(complete)
        meta_path.write_text(
            json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    return paths_out


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
        path = chapter_path(book_dir, chapter_number(chapter["chapter"]))
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
