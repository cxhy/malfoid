"""Migrate a legacy chunk preview into segment state and render it (ticket 06).

The preview source is re-segmented into chapters of segments. Existing legacy
translations are indexed by source hash: a polished chunk is preferred over its
draft, and a chunk whose paragraph count does not line up with its source is
skipped rather than guessed. Only segments with a matching source hash reuse a
legacy target; every other segment is left pending for retranslation.

Markdown and HTML are rendered from segment state. Continuation parts are merged
back into one paragraph, so rendering never changes the logical paragraph count.
"""

from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import batch_translate
import polish_batch
import segment_state

PREVIEW_DIR = "preview"
MIGRATION_REPORT = "preview_migration.json"
PREVIEW_MARKDOWN = "book.md"
PREVIEW_HTML = "book.html"

_CHAPTER_SORT_RE = re.compile(r"(\d+)$")
_HEADING_PREFIX_RE = re.compile(r"^\s*#{1,6}\s+")
_PARAGRAPH_SPLIT_RE = re.compile(r"\n\s*\n")


@dataclass(frozen=True)
class MigrationResult:
    """Summary of one legacy preview migration."""

    book_dir: Path
    chapters: list[Path]
    source_paths: list[Path]
    chunk_ids: list[str]
    reused: int
    reused_polished: int
    reused_draft: int
    unmatched: int
    skipped_chunks: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "book_dir": str(self.book_dir),
            "chapters": [str(path) for path in self.chapters],
            "source_paths": [str(path) for path in self.source_paths],
            "chunk_ids": list(self.chunk_ids),
            "reused": self.reused,
            "reused_polished": self.reused_polished,
            "reused_draft": self.reused_draft,
            "unmatched": self.unmatched,
            "skipped_chunks": list(self.skipped_chunks),
        }


@dataclass(frozen=True)
class TranslationResult:
    """Summary of retranslating pending segments."""

    translated: int
    failed: int
    skipped: int
    chapters: int
    failures: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "translated": self.translated,
            "failed": self.failed,
            "skipped": self.skipped,
            "chapters": self.chapters,
            "failures": list(self.failures),
        }


@dataclass(frozen=True)
class PolishResult:
    """Summary of polishing targets that were not already polished."""

    polished: int
    failed: int
    skipped: int
    chapters: int
    failures: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "polished": self.polished,
            "failed": self.failed,
            "skipped": self.skipped,
            "chapters": self.chapters,
            "failures": list(self.failures),
        }


@dataclass(frozen=True)
class RenderResult:
    """Paths written by one preview render."""

    markdown: Path
    html: Path


@dataclass(frozen=True)
class PreviewResult:
    """Combined migration, model steps and render result."""

    migration: MigrationResult
    translation: TranslationResult | None
    polish: PolishResult | None
    markdown: Path
    html: Path


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def split_paragraphs(text: str) -> list[str]:
    """Split Markdown/plain text on blank lines, preserving paragraph text."""
    return [part.strip() for part in _PARAGRAPH_SPLIT_RE.split(text.strip()) if part.strip()]


def _normalize_chunk_id(value: int | str) -> str:
    if isinstance(value, int):
        number = value
    else:
        match = re.search(r"(\d+)$", str(value))
        if not match:
            raise ValueError(f"invalid chunk id: {value}")
        number = int(match.group(1))
    return f"chunk{number:04d}"


def discover_chunk_ids(work_dir: Path) -> list[str]:
    """Find legacy source chunks that have a draft or polished translation."""
    work = Path(work_dir)
    ids: list[str] = []
    for source in sorted((work / "work_temp").glob("chunk*.md")):
        if source.name.startswith("output_"):
            continue
        chunk_id = source.stem
        polished = work / "translation" / "polished" / f"{chunk_id}.md"
        draft = work / "work_temp" / f"output_{chunk_id}.md"
        if polished.is_file() or draft.is_file():
            ids.append(chunk_id)
    return ids


def _source_chunk_path(work_dir: Path, chunk_id: str) -> Path:
    return Path(work_dir) / "work_temp" / f"{chunk_id}.md"


def _translation_candidates(work_dir: Path, chunk_id: str) -> list[tuple[Path, str]]:
    return [
        (Path(work_dir) / "translation" / "polished" / f"{chunk_id}.md", "polished"),
        (Path(work_dir) / "work_temp" / f"output_{chunk_id}.md", "draft"),
    ]


def _legacy_source_segments(path: Path) -> list[dict[str, Any]]:
    text = Path(path).read_text(encoding="utf-8")
    chapters = segment_state.parse_source(text, path.stem, max_segment_tokens=None)
    return [segment for chapter in chapters for segment in chapter.get("segments") or []]


def _translation_targets(path: Path, source_segments: list[dict[str, Any]]) -> list[str] | None:
    """Align one legacy translation file to its source segments.

    Returns ``None`` when the paragraph counts differ, because positional reuse
    would attach text to the wrong source hash.
    """
    paragraphs = split_paragraphs(Path(path).read_text(encoding="utf-8"))
    if len(paragraphs) != len(source_segments):
        return None
    targets: list[str] = []
    for segment, paragraph in zip(source_segments, paragraphs):
        text = paragraph.strip()
        if segment.get("kind") == "heading":
            text = _HEADING_PREFIX_RE.sub("", text).strip()
        targets.append(text)
    return targets


def build_reuse_map(
    work_dir: Path, chunk_ids: list[int | str]
) -> tuple[dict[str, dict[str, str]], list[str]]:
    """Index legacy targets by source hash, preferring polished chunks.

    A chunk whose source and translation paragraph counts differ is skipped and
    reported; its translations are never reused by position.
    """
    reuse: dict[str, dict[str, str]] = {}
    skipped: list[str] = []
    for chunk_id in sorted({_normalize_chunk_id(value) for value in chunk_ids}):
        source_path = _source_chunk_path(work_dir, chunk_id)
        if not source_path.is_file():
            skipped.append(chunk_id)
            continue
        source_segments = _legacy_source_segments(source_path)
        aligned: list[str] | None = None
        origin = ""
        for path, candidate_origin in _translation_candidates(work_dir, chunk_id):
            if not path.is_file():
                continue
            aligned = _translation_targets(path, source_segments)
            if aligned is not None:
                origin = candidate_origin
                break
        if aligned is None:
            skipped.append(chunk_id)
            continue
        for segment, target in zip(source_segments, aligned):
            if not target:
                continue
            key = segment.get("source_hash") or segment_state.source_hash(segment["source"])
            existing = reuse.get(key)
            if existing is None or (existing["origin"] == "draft" and origin == "polished"):
                reuse[key] = {"target": target, "origin": origin}
    return reuse, skipped


def _archive_stale_chapters(chapters_dir: Path, keep: set[Path]) -> None:
    stale = [path for path in chapters_dir.glob("ch-*.json") if path not in keep]
    if not stale:
        return
    archive = chapters_dir / "archive" / _now()
    archive.mkdir(parents=True, exist_ok=True)
    for path in stale:
        path.replace(archive / path.name)


def _write_migration_report(book_dir: Path, result: MigrationResult) -> Path:
    path = book_dir / "work_temp" / MIGRATION_REPORT
    path.parent.mkdir(parents=True, exist_ok=True)
    data = result.as_dict()
    data["created_at"] = _now()
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def migrate_preview(
    work_dir: Path,
    chunk_ids: list[int | str] | None = None,
    *,
    book_dir: Path | None = None,
    source_paths: list[Path] | None = None,
    max_segment_tokens: int | None = segment_state.DEFAULT_SEGMENT_TOKEN_BUDGET,
) -> MigrationResult:
    """Re-segment a preview source and reuse legacy targets by source hash.

    ``source_paths`` defaults to the selected legacy source chunks. Passing a
    different source is how a changed paragraph is detected: its new hash does not
    match the legacy index, so it stays pending instead of inheriting stale text.
    """
    work = Path(work_dir)
    ids = [_normalize_chunk_id(value) for value in (chunk_ids or discover_chunk_ids(work))]
    if not ids:
        raise ValueError(f"no translated legacy chunks found under {work}")
    paths = (
        [Path(path) for path in source_paths]
        if source_paths
        else [_source_chunk_path(work, chunk_id) for chunk_id in ids]
    )
    missing = [path for path in paths if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"missing preview source: {missing[0]}")

    source_text = "\n\n".join(path.read_text(encoding="utf-8").strip() for path in paths)
    fallback_title = paths[0].stem
    chapters = segment_state.parse_source(
        source_text, fallback_title, max_segment_tokens=max_segment_tokens
    )
    reuse, skipped = build_reuse_map(work, ids)

    reused = reused_polished = reused_draft = unmatched = 0
    for chapter in chapters:
        for segment in chapter.get("segments") or []:
            entry = reuse.get(segment.get("source_hash") or "")
            if entry is None:
                segment["target"] = None
                segment["target_origin"] = None
                unmatched += 1
                continue
            segment["target"] = entry["target"]
            segment["target_origin"] = entry["origin"]
            reused += 1
            if entry["origin"] == "polished":
                reused_polished += 1
            else:
                reused_draft += 1

    target_book = Path(book_dir) if book_dir is not None else work
    chapters_dir = target_book / "source" / segment_state.CHAPTERS_DIR
    chapters_dir.mkdir(parents=True, exist_ok=True)
    chapter_paths: list[Path] = []
    for chapter in chapters:
        path = segment_state.chapter_path(target_book, int(chapter["chapter"]))
        segment_state.save_chapter(path, chapter)
        chapter_paths.append(path)
    _archive_stale_chapters(chapters_dir, set(chapter_paths))

    result = MigrationResult(
        book_dir=target_book,
        chapters=chapter_paths,
        source_paths=paths,
        chunk_ids=ids,
        reused=reused,
        reused_polished=reused_polished,
        reused_draft=reused_draft,
        unmatched=unmatched,
        skipped_chunks=skipped,
    )
    _write_migration_report(target_book, result)
    return result


def _chapter_sort_key(path: Path) -> int:
    match = _CHAPTER_SORT_RE.search(path.stem)
    return int(match.group(1)) if match else 0


def book_chapter_paths(book_dir: Path) -> list[Path]:
    chapters_dir = Path(book_dir) / "source" / segment_state.CHAPTERS_DIR
    return sorted(chapters_dir.glob("ch-*.json"), key=_chapter_sort_key)


def load_chapters(book_dir: Path) -> list[dict[str, Any]]:
    return [segment_state.load_chapter(path) for path in book_chapter_paths(book_dir)]


def save_chapter(path: Path, chapter: dict[str, Any]) -> Path:
    return segment_state.save_chapter(path, chapter)


def _has_target(segment: dict[str, Any]) -> bool:
    target = segment.get("target")
    return isinstance(target, str) and bool(target.strip())


def _mark_translated(chapter: dict[str, Any], results: list[dict[str, Any]]) -> None:
    by_index = {segment.get("index"): segment for segment in chapter.get("segments") or []}
    for result in results:
        segment = by_index.get(result.get("index"))
        if segment is None or result.get("source_hash") != segment.get("source_hash"):
            continue
        if isinstance(result.get("target"), str) and result["target"].strip():
            segment["target_origin"] = "translated"


def _failures(chapter: dict[str, Any], results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    chapter_number = int(chapter.get("chapter") or 0)
    failures = []
    for result in results:
        if isinstance(result.get("target"), str) and result["target"].strip():
            continue
        failures.append(
            {
                "chapter": chapter_number,
                "index": result.get("index"),
                "error": result.get("error") or "no target",
                "stage": result.get("stage"),
            }
        )
    return failures


def translate_missing(
    book_dir: Path,
    client: Any,
    *,
    budget: int = batch_translate.DEFAULT_BATCH_BUDGET,
    measure=None,
    max_retries: int = batch_translate.MAX_PROTOCOL_RETRIES,
) -> TranslationResult:
    """Translate only segments that still have no target."""
    translated = failed = skipped = chapters_done = 0
    failures: list[dict[str, Any]] = []
    for path in book_chapter_paths(book_dir):
        chapter = segment_state.load_chapter(path)
        pending = [segment for segment in chapter.get("segments") or [] if not _has_target(segment)]
        if not pending:
            continue
        results = batch_translate.translate_batch(
            client, pending, budget=budget, measure=measure, max_retries=max_retries
        )
        stats = batch_translate.apply_targets(chapter, results)
        _mark_translated(chapter, results)
        segment_state.save_chapter(path, chapter)
        translated += stats["applied"]
        failed += stats["failed"]
        skipped += stats["skipped"]
        failures.extend(_failures(chapter, results))
        chapters_done += 1
    return TranslationResult(
        translated=translated,
        failed=failed,
        skipped=skipped,
        chapters=chapters_done,
        failures=failures,
    )


def _mark_polished(chapter: dict[str, Any], results: list[dict[str, Any]]) -> None:
    by_index = {segment.get("index"): segment for segment in chapter.get("segments") or []}
    for result in results:
        segment = by_index.get(result.get("index"))
        if segment is None or result.get("source_hash") != segment.get("source_hash"):
            continue
        if isinstance(result.get("target"), str) and result["target"].strip():
            segment["target_origin"] = "polished"


def polish_pending(
    book_dir: Path,
    client: Any,
    *,
    budget: int = polish_batch.DEFAULT_BATCH_BUDGET,
    measure=None,
    max_retries: int = polish_batch.MAX_PROTOCOL_RETRIES,
) -> PolishResult:
    """Polish targets that were not already reused from a polished chunk."""
    polished = failed = skipped = chapters_done = 0
    failures: list[dict[str, Any]] = []
    for path in book_chapter_paths(book_dir):
        chapter = segment_state.load_chapter(path)
        pending = [
            segment
            for segment in chapter.get("segments") or []
            if _has_target(segment) and segment.get("target_origin") != "polished"
        ]
        if not pending:
            continue
        results = polish_batch.polish_batch(
            client, pending, budget=budget, measure=measure, max_retries=max_retries
        )
        stats = polish_batch.apply_polished(chapter, results)
        _mark_polished(chapter, results)
        segment_state.save_chapter(path, chapter)
        polished += stats["applied"]
        failed += stats["failed"]
        skipped += stats["skipped"]
        failures.extend(_failures(chapter, results))
        chapters_done += 1
    return PolishResult(
        polished=polished,
        failed=failed,
        skipped=skipped,
        chapters=chapters_done,
        failures=failures,
    )


def _display_text(segment: dict[str, Any]) -> str:
    target = segment.get("target")
    if isinstance(target, str) and target.strip():
        return target.strip()
    return (segment.get("source") or "").strip()


def logical_target_blocks(chapter: dict[str, Any]) -> list[dict[str, Any]]:
    """Collapse target text into logical blocks, merging continuation parts."""
    blocks: list[dict[str, Any]] = []
    for segment in chapter.get("segments") or []:
        text = _display_text(segment)
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


def render_chapter_markdown(chapter: dict[str, Any]) -> str:
    parts: list[str] = []
    for block in logical_target_blocks(chapter):
        if block["kind"] == "heading":
            level = int(block.get("level") or chapter.get("heading_level") or 1)
            text = _HEADING_PREFIX_RE.sub("", block["text"]).strip()
            parts.append(f"{'#' * level} {text}")
        else:
            parts.append(block["text"])
    return "\n\n".join(parts)


def render_book_markdown(book_dir: Path) -> str:
    chapters = load_chapters(book_dir)
    return "\n\n".join(render_chapter_markdown(chapter) for chapter in chapters) + "\n"


def _inline_html(text: str) -> str:
    escaped = html.escape(text)
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped, flags=re.DOTALL)
    escaped = re.sub(r"\*(.+?)\*", r"<em>\1</em>", escaped, flags=re.DOTALL)
    escaped = re.sub(r"`(.+?)`", r"<code>\1</code>", escaped, flags=re.DOTALL)
    escaped = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', escaped)
    return escaped


def render_chapter_html(chapter: dict[str, Any]) -> str:
    parts: list[str] = []
    for block in logical_target_blocks(chapter):
        if block["kind"] == "heading":
            level = int(block.get("level") or chapter.get("heading_level") or 1)
            level = min(max(level, 1), 6)
            text = _HEADING_PREFIX_RE.sub("", block["text"]).strip()
            parts.append(f"<h{level}>{_inline_html(text)}</h{level}>")
        else:
            parts.append(f"<p>{_inline_html(block['text'])}</p>")
    return "\n".join(parts)


def render_book_html(book_dir: Path, title: str | None = None, author: str | None = None) -> str:
    book = Path(book_dir)
    meta_path = book / "meta.json"
    meta: dict[str, Any] = {}
    if meta_path.is_file():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    resolved_title = title or meta.get("title") or book.name
    resolved_author = author if author is not None else meta.get("author") or ""
    chapters = load_chapters(book)
    body = "\n".join(render_chapter_html(chapter) for chapter in chapters)
    author_html = (
        f'<div class="book-author">{html.escape(resolved_author)}</div>' if resolved_author else ""
    )
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{html.escape(resolved_title)}</title>
<style>
body {{ font-family: 'FangSong', 'STFangSong', serif; line-height: 1.8; color: #333;
       max-width: 720px; margin: 0 auto; padding: 2.5rem 1.5rem; background: #fefefe; }}
h1, h2, h3 {{ line-height: 1.4; margin: 1.6rem 0 0.8rem; }}
p {{ margin: 0 0 1rem; white-space: pre-wrap; text-align: justify; }}
.book-title {{ text-align: center; margin-top: 0; }}
.book-author {{ text-align: center; color: #666; }}
</style>
</head>
<body>
<article>
<h1 class="book-title">{html.escape(resolved_title)}</h1>
{author_html}
{body}
</article>
</body>
</html>
"""


def write_preview(
    book_dir: Path,
    output_dir: Path | None = None,
    *,
    title: str | None = None,
    author: str | None = None,
) -> RenderResult:
    """Render segment state to one Markdown and one HTML preview file."""
    book = Path(book_dir)
    out = Path(output_dir) if output_dir is not None else book / "translation" / PREVIEW_DIR
    out.mkdir(parents=True, exist_ok=True)
    markdown_path = out / PREVIEW_MARKDOWN
    html_path = out / PREVIEW_HTML
    markdown_path.write_text(render_book_markdown(book), encoding="utf-8")
    html_path.write_text(render_book_html(book, title=title, author=author), encoding="utf-8")
    return RenderResult(markdown=markdown_path, html=html_path)


def run_preview(
    work_dir: Path,
    chunk_ids: list[int | str] | None = None,
    *,
    book_dir: Path | None = None,
    source_paths: list[Path] | None = None,
    client: Any = None,
    polish: bool = True,
    output_dir: Path | None = None,
    budget: int = batch_translate.DEFAULT_BATCH_BUDGET,
    max_segment_tokens: int | None = segment_state.DEFAULT_SEGMENT_TOKEN_BUDGET,
) -> PreviewResult:
    """Migrate, retranslate missing segments, polish, and render the preview."""
    migration = migrate_preview(
        work_dir,
        chunk_ids,
        book_dir=book_dir,
        source_paths=source_paths,
        max_segment_tokens=max_segment_tokens,
    )
    translation: TranslationResult | None = None
    polish_result: PolishResult | None = None
    if client is not None:
        translation = translate_missing(migration.book_dir, client, budget=budget)
        if polish:
            polish_result = polish_pending(migration.book_dir, client, budget=budget)
    elif migration.unmatched:
        raise ValueError(
            f"{migration.unmatched} segment(s) are untranslated; a model client is required"
        )

    render = write_preview(migration.book_dir, output_dir=output_dir)
    return PreviewResult(
        migration=migration,
        translation=translation,
        polish=polish_result,
        markdown=render.markdown,
        html=render.html,
    )
