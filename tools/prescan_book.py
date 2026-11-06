"""Per-book prescan: parallel chapter digests, hash caching and book artifacts.

Prescan runs only after the whole book source has been ingested into chapter
state. Chapter digests are generated in parallel, cached by chapter source hash
under ``work_temp/prescan``, and reused without a model call when the chapter is
unchanged. The complete book then yields a style brief, a synopsis and a book
story bible. When a book is finished its story bible is merged into the
series-level ``story_bible.md``.

Preview translation skips prescan: use :func:`should_prescan` with
``preview=True`` and translate from the glossary plus neighbour context instead.
"""

from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, replace
from pathlib import Path

import book_state
import segment_state
from model_client import ask_text

PRESCAN_DIR = "prescan"
DIGESTS_DIR = "digests"
STYLE_BRIEF_FILE = "style_brief.md"
SYNOPSIS_FILE = "book_synopsis.md"
STORY_BIBLE_FILE = "story_bible.md"
CHAPTER_DIGESTS_DIR = "chapter_digests"

DEFAULT_WORKERS = 2

DIGEST_TASK = "TASK: DIGEST"
STYLE_BRIEF_TASK = "TASK: STYLE_BRIEF"
SYNOPSIS_TASK = "TASK: SYNOPSIS"
STORY_BIBLE_TASK = "TASK: STORY_BIBLE"


class PrescanNotReadyError(RuntimeError):
    """Raised when prescan is requested before the whole book source is present."""


class PrescanError(RuntimeError):
    """Raised when the model boundary returns unusable prescan output."""


@dataclass(frozen=True)
class PrescanResult:
    """Paths and digest records produced by one prescan run."""

    digests: list[dict] = field(default_factory=list)
    style_brief: Path | None = None
    synopsis: Path | None = None
    story_bible: Path | None = None
    series_bible: Path | None = None


def prescan_dir(book_dir: Path) -> Path:
    return book_dir / "work_temp" / PRESCAN_DIR


def digests_dir(book_dir: Path) -> Path:
    return prescan_dir(book_dir) / DIGESTS_DIR


def digest_cache_path(book_dir: Path, source_hash: str) -> Path:
    return digests_dir(book_dir) / f"{source_hash}.md"


def chapter_digest_path(book_dir: Path, number: int) -> Path:
    return prescan_dir(book_dir) / CHAPTER_DIGESTS_DIR / f"ch-{number:02d}.md"


def style_brief_path(book_dir: Path) -> Path:
    return prescan_dir(book_dir) / STYLE_BRIEF_FILE


def synopsis_path(book_dir: Path) -> Path:
    return prescan_dir(book_dir) / SYNOPSIS_FILE


def story_bible_path(book_dir: Path) -> Path:
    return prescan_dir(book_dir) / STORY_BIBLE_FILE


def _read_meta(book_dir: Path) -> dict:
    path = book_dir / "meta.json"
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _meta_chapter_numbers(meta: dict) -> list[int]:
    numbers: list[int] = []
    for entry in meta.get("chapters") or []:
        index = entry.get("index")
        if index is None:
            return []
        numbers.append(int(index))
    return sorted(numbers)


def book_source_ready(book_dir: Path) -> bool:
    """Return True when every chapter in the book has chapter state.

    A partially ingested source, or a book whose metadata promises chapters that
    have no state file, is not ready for prescan.
    """
    meta = _read_meta(book_dir)
    if meta.get("source_complete") is not True:
        return False
    numbers = _meta_chapter_numbers(meta)
    if not numbers:
        return False
    if int(meta.get("chapter_count") or 0) != len(numbers):
        return False
    for number in numbers:
        path = segment_state.chapter_path(book_dir, number)
        if not path.is_file():
            return False
        try:
            chapter = segment_state.load_chapter(path)
        except (OSError, ValueError):
            return False
        if not chapter.get("segments"):
            return False
    return True


def should_prescan(book_dir: Path, preview: bool = False) -> bool:
    """Gate prescan on source completeness; preview runs never prescan."""
    if preview:
        return False
    return book_source_ready(book_dir)


def _require_ready(book_dir: Path) -> None:
    if not book_source_ready(book_dir):
        raise PrescanNotReadyError(
            f"book source is incomplete or missing chapter state: {book_dir}"
        )


def chapter_source_text(chapter: dict) -> str:
    return segment_state.chapter_text(chapter)


def chapter_source_hash(chapter: dict) -> str:
    return segment_state.source_hash(chapter_source_text(chapter))


def _cached_digest(path: Path) -> str | None:
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return text or None


def plan_digests(book_dir: Path) -> list[dict]:
    """Plan one digest per chapter and mark cache hits by source hash."""
    _require_ready(book_dir)
    meta = _read_meta(book_dir)
    plan: list[dict] = []
    for number in _meta_chapter_numbers(meta):
        chapter = segment_state.load_chapter(segment_state.chapter_path(book_dir, number))
        source_hash = chapter_source_hash(chapter)
        cache_path = digest_cache_path(book_dir, source_hash)
        plan.append(
            {
                "chapter": int(chapter.get("chapter") or number),
                "title": chapter.get("title") or "",
                "source_hash": source_hash,
                "source_text": chapter_source_text(chapter),
                "cache_path": cache_path,
                "chapter_path": chapter_digest_path(
                    book_dir, int(chapter.get("chapter") or number)
                ),
                "cached": _cached_digest(cache_path) is not None,
            }
        )
    return plan


def _ask(client: object, prompt: str) -> str:
    try:
        return ask_text(client, prompt)
    except ValueError as exc:
        raise PrescanError(str(exc)) from None


def _digest_prompt(entry: dict) -> str:
    return (
        f"{DIGEST_TASK}\n"
        "Write a compact digest of this chapter for a translator's book prescan. "
        "Cover plot, character states, setting, recurring terms and tone. "
        "Do not translate or rewrite the chapter.\n"
        f"Chapter: {entry['title']}\n"
        f"Number: {entry['chapter']}\n\n"
        f"{entry['source_text']}"
    )


def _generate_digest(client: object, entry: dict) -> tuple[str, str]:
    digest = _ask(client, _digest_prompt(entry))
    cache_path = entry["cache_path"]
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(digest + "\n", encoding="utf-8")
    chapter_path = entry.get("chapter_path")
    if chapter_path is not None:
        chapter_path.parent.mkdir(parents=True, exist_ok=True)
        chapter_path.write_text(digest + "\n", encoding="utf-8")
    return entry["source_hash"], digest


def run_digests(book_dir: Path, client: object, workers: int = DEFAULT_WORKERS) -> list[dict]:
    """Generate or reuse chapter digests, returning them in chapter order."""
    if workers < 1:
        raise ValueError("workers must be at least 1")
    entries = plan_digests(book_dir)

    pending: dict[str, dict] = {}
    for entry in entries:
        if entry["cached"]:
            entry["digest"] = _cached_digest(entry["cache_path"]) or ""
            entry["generated"] = False
            chapter_path = entry.get("chapter_path")
            if chapter_path is not None:
                chapter_path.parent.mkdir(parents=True, exist_ok=True)
                chapter_path.write_text(entry["digest"] + "\n", encoding="utf-8")
        elif entry["source_hash"] not in pending:
            pending[entry["source_hash"]] = entry

    generated: dict[str, str] = {}
    if pending:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(_generate_digest, client, entry): source_hash
                for source_hash, entry in pending.items()
            }
            for future in as_completed(futures):
                source_hash, digest = future.result()
                generated[source_hash] = digest

    for entry in entries:
        if not entry["cached"]:
            entry["digest"] = generated[entry["source_hash"]]
            entry["generated"] = True
    return entries


def _digest_context(digests: list[dict]) -> str:
    parts = []
    for entry in digests:
        parts.append(f"## Chapter {entry['chapter']}: {entry['title']}\n\n{entry['digest']}")
    return "\n\n".join(parts)


def _book_title(book_dir: Path) -> str:
    return str(_read_meta(book_dir).get("title") or book_dir.name)


def _write_artifact(path: Path, title: str, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"# {title}\n\n{body.strip()}\n", encoding="utf-8")
    return path


def _merge_context(context: str) -> str:
    return f"Use this complete book digest as the only source:\n\n{context}"


def _ask_cached(client: object, cache_path: Path, prompt: str) -> str:
    if cache_path.is_file():
        cached = cache_path.read_text(encoding="utf-8").strip()
        if cached:
            return cached
    text = _ask(client, prompt)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(text + "\n", encoding="utf-8")
    return text


def run_prescan(
    book_dir: Path,
    client: object,
    workers: int = DEFAULT_WORKERS,
    series_root: Path | None = None,
) -> PrescanResult:
    """Build digests, style brief, synopsis and book bible for a complete book.

    When ``series_root`` is given the finished book bible is merged into the
    series-level bible immediately.
    """
    _require_ready(book_dir)
    digests = run_digests(book_dir, client, workers=workers)
    context = _digest_context(digests)
    title = _book_title(book_dir)
    context_hash = segment_state.source_hash(context)
    cache_dir = prescan_dir(book_dir) / "cache"

    style = _ask_cached(
        client,
        cache_dir / f"{context_hash}-style.md",
        f"{STYLE_BRIEF_TASK}\n"
        "Describe the book's style for a translator: register, rhythm, "
        "sentence length, dialogue habits, recurring imagery and tone.\n\n"
        f"{_merge_context(context)}",
    )
    synopsis = _ask_cached(
        client,
        cache_dir / f"{context_hash}-synopsis.md",
        f"{SYNOPSIS_TASK}\n"
        "Write a book synopsis for a translator: plot order, turning points, "
        "ending state and relationships. Do not import canon events.\n\n"
        f"{_merge_context(context)}",
    )
    bible = _ask_cached(
        client,
        cache_dir / f"{context_hash}-bible.md",
        f"{STORY_BIBLE_TASK}\n"
        "Write a book story bible for a translator: characters, names, pronouns, "
        "settings, timeline, AU rules and canon divergences.\n\n"
        f"{_merge_context(context)}",
    )

    result = PrescanResult(
        digests=digests,
        style_brief=_write_artifact(style_brief_path(book_dir), f"Style brief: {title}", style),
        synopsis=_write_artifact(synopsis_path(book_dir), f"Synopsis: {title}", synopsis),
        story_bible=_write_artifact(story_bible_path(book_dir), f"Story bible: {title}", bible),
    )
    if series_root is None:
        candidate = book_dir.parent.parent
        if book_state.is_series_root(candidate):
            series_root = candidate
    if series_root is not None:
        result = replace(result, series_bible=merge_story_bible(series_root, book_dir))
    return result


def _bible_markers(book_dir: Path) -> tuple[str, str]:
    slug = book_dir.name
    start = f"<!-- {slug}:start -->"
    end = f"<!-- {slug}:end -->"
    return start, end


def merge_story_bible(series_root: Path, book_dir: Path) -> Path:
    """Merge one book's story bible into the series bible, replacing its section."""
    bible_path = story_bible_path(book_dir)
    if not bible_path.is_file():
        raise FileNotFoundError(f"book story bible not found: {bible_path}")
    if not book_state.is_series_root(series_root):
        raise FileNotFoundError(f"not a series root: {series_root}")

    series_path = series_root / book_state.SERIES_BIBLE
    existing = (
        series_path.read_text(encoding="utf-8")
        if series_path.is_file()
        else "# Series story bible\n"
    )
    start, end = _bible_markers(book_dir)
    body = bible_path.read_text(encoding="utf-8").strip()
    block = f"{start}\n{body}\n{end}"
    pattern = re.compile(re.escape(start) + r".*?" + re.escape(end), re.DOTALL)
    if pattern.search(existing):
        merged = pattern.sub(lambda _match: block, existing, count=1)
    else:
        merged = existing.rstrip() + "\n\n" + block
    if not merged.endswith("\n"):
        merged += "\n"
    series_path.write_text(merged, encoding="utf-8")
    return series_path
