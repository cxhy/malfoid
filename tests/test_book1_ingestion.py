"""Book 1 multi-source ingestion and prescan wiring tests (ticket 12)."""

from __future__ import annotations

import json
import re
from pathlib import Path

import book_state
import prescan_book
import segment_state

SEGMENT_ONE = """=== Book 1: Chapter 5-7 ===

Chapter five opening paragraph.

Still chapter five.

Ch 6

Chapter six opening paragraph.

Ch 7

Chapter seven opening paragraph.
"""

SEGMENT_TWO = """=== Book 1: Chapters 8-12 ===

Ch 8

Chapter eight opening paragraph.

Ch 9

Chapter nine opening paragraph.

Ch 10

Chapter ten opening paragraph.

Ch 12

Chapter twelve opening paragraph.
"""

SEGMENT_THREE = """=== Book 1: Chapters 13-17 ===

Ch 13

Chapter thirteen opening paragraph.

Ch 14

Chapter fourteen opening paragraph.

Ch 15

Chapter fifteen opening paragraph.

Chapter 16 is omitted in this fixture.

Ch 17

Chapter seventeen opening paragraph.
"""

OFFSET_ONE = """=== Segment 001 ===

Opening paragraph.

Ch 2

Chapter two paragraph.

Ch 3

Chapter three paragraph.
"""

OFFSET_TWO = """=== Segment 002 ===

Ch 4

Chapter four paragraph.
"""

EXPECTED_CHAPTERS = [5, 6, 7, 8, 9, 10, 12, 13, 14, 15, 17]
EXPECTED_TITLES = [f"Ch {number}" for number in EXPECTED_CHAPTERS]


def _series(tmp_path: Path) -> Path:
    root = tmp_path / "series"
    book_state.create_series(
        root,
        series_id="hp-tsundere",
        title="Harry Potter and the Sorcerer's Tsundere",
        author="GuardBro",
        books=[{"number": 1, "title": "Book 1"}],
    )
    return root


def _sources(tmp_path: Path) -> list[Path]:
    paths = []
    for name, text in (
        ("001 Book 1: Chapter 5-7.txt", SEGMENT_ONE),
        ("002 Book 1: Chapters 8-12.txt", SEGMENT_TWO),
        ("003 Book 1: Chapters 13-17.txt", SEGMENT_THREE),
    ):
        path = tmp_path / name
        path.write_text(text, encoding="utf-8")
        paths.append(path)
    return paths


def _ingest_book1(tmp_path: Path) -> tuple[Path, Path]:
    root = _series(tmp_path)
    book = book_state.resolve_book(root, 1)
    segment_state.ingest_sources(book, _sources(tmp_path))
    return root, book


def _chapter_title(prompt: str) -> str:
    match = re.search(r"^Chapter: (.+)$", prompt, re.MULTILINE)
    return match.group(1) if match else "unknown"


class FakeClient:
    """Deterministic offline prescan client with digest call accounting."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.digest_calls = 0

    def __call__(self, prompt: str) -> str:
        self.calls.append(prompt)
        if "TASK: DIGEST" in prompt:
            self.digest_calls += 1
            return f"digest-of-{_chapter_title(prompt)}"
        if "TASK: STYLE_BRIEF" in prompt:
            return "STYLE BRIEF BODY"
        if "TASK: SYNOPSIS" in prompt:
            return "SYNOPSIS BODY"
        if "TASK: STORY_BIBLE" in prompt:
            return "STORY BIBLE BODY"
        return "unknown task"


def test_three_segments_ingest_as_continuous_chapters(tmp_path: Path) -> None:
    root, book = _ingest_book1(tmp_path)
    meta = json.loads((book / "meta.json").read_text(encoding="utf-8"))

    assert meta["source_complete"] is True
    assert meta["chapter_count"] == len(EXPECTED_CHAPTERS)
    assert [record["index"] for record in meta["chapters"]] == EXPECTED_CHAPTERS
    assert [record["title"] for record in meta["chapters"]] == EXPECTED_TITLES
    assert meta["source_files"] == [str(path) for path in _sources(tmp_path)]

    for number in EXPECTED_CHAPTERS:
        path = segment_state.chapter_path(book, number)
        assert path.is_file()
        chapter = segment_state.load_chapter(path)
        assert chapter["chapter"] == number
        assert chapter["title"] == f"Ch {number}"
        assert chapter["segments"][0]["kind"] == "heading"
        assert chapter["segments"][0]["source"] == f"Ch {number}"
    assert not segment_state.chapter_path(book, 11).exists()
    assert not segment_state.chapter_path(book, 16).exists()
    assert root.is_dir()


def test_paragraph_boundaries_and_marker_gaps_are_preserved(tmp_path: Path) -> None:
    _, book = _ingest_book1(tmp_path)
    first = segment_state.load_chapter(segment_state.chapter_path(book, 5))
    assert [segment["source"] for segment in first["segments"]] == [
        "Ch 5",
        "Chapter five opening paragraph.",
        "Still chapter five.",
    ]
    assert [segment["index"] for segment in first["segments"]] == [0, 1, 2]

    fifteen = segment_state.load_chapter(segment_state.chapter_path(book, 15))
    assert fifteen["segments"][-1]["kind"] == "text"
    assert fifteen["segments"][-1]["source"] == "Chapter 16 is omitted in this fixture."

    rendered = segment_state.render_book(book)
    assert rendered.startswith("# Ch 5\n\nChapter five opening paragraph.")
    assert rendered.index("# Ch 12") < rendered.index("# Ch 13")
    assert "# Ch 16" not in rendered


def test_chapter_records_match_paragraph_counts_and_source_hashes(tmp_path: Path) -> None:
    _, book = _ingest_book1(tmp_path)
    meta = json.loads((book / "meta.json").read_text(encoding="utf-8"))
    records = {record["index"]: record for record in meta["chapters"]}

    assert records[5]["paragraph_count"] == 2
    assert records[6]["paragraph_count"] == 1
    assert records[15]["paragraph_count"] == 2
    assert records[5]["segment_count"] == 3

    for number in EXPECTED_CHAPTERS:
        chapter = segment_state.load_chapter(segment_state.chapter_path(book, number))
        text = segment_state.chapter_text(chapter)
        assert records[number]["source_hash"] == segment_state.source_hash(text)
        for segment in chapter["segments"]:
            assert segment["source_hash"] == segment_state.source_hash(segment["source"])
            assert segment["target"] is None
    assert (book / "translation").is_dir()
    assert list((book / "translation").iterdir()) == []


def test_chapter_offset_continues_numbering_across_sources(tmp_path: Path) -> None:
    root = _series(tmp_path)
    book = book_state.resolve_book(root, 1)
    first = tmp_path / "offset-1.txt"
    second = tmp_path / "offset-2.txt"
    first.write_text(OFFSET_ONE, encoding="utf-8")
    second.write_text(OFFSET_TWO, encoding="utf-8")

    segment_state.ingest_sources(book, [first, second], chapter_offset=4)
    meta = json.loads((book / "meta.json").read_text(encoding="utf-8"))
    assert [record["index"] for record in meta["chapters"]] == [5, 6, 7, 8]
    assert [record["title"] for record in meta["chapters"]] == [
        "Ch 5",
        "Ch 6",
        "Ch 7",
        "Ch 8",
    ]


def test_reingesting_the_same_sources_keeps_metadata_stable(tmp_path: Path) -> None:
    _, book = _ingest_book1(tmp_path)
    sources = _sources(tmp_path)
    segment_state.ingest_sources(book, sources)
    meta = json.loads((book / "meta.json").read_text(encoding="utf-8"))
    assert meta["chapter_count"] == len(EXPECTED_CHAPTERS)
    assert meta["source_files"] == [str(path) for path in sources]


def test_prescan_runs_on_the_ingested_book_with_cache_reuse(tmp_path: Path) -> None:
    root, book = _ingest_book1(tmp_path)
    assert prescan_book.should_prescan(book) is True

    client = FakeClient()
    result = prescan_book.run_prescan(book, client, workers=2, series_root=root)
    assert [entry["chapter"] for entry in result.digests] == EXPECTED_CHAPTERS
    assert [entry["title"] for entry in result.digests] == EXPECTED_TITLES
    assert client.digest_calls == len(EXPECTED_CHAPTERS)
    assert result.style_brief == prescan_book.style_brief_path(book)
    assert result.synopsis == prescan_book.synopsis_path(book)
    assert result.story_bible == prescan_book.story_bible_path(book)
    assert "STYLE BRIEF BODY" in result.style_brief.read_text(encoding="utf-8")
    assert "SYNOPSIS BODY" in result.synopsis.read_text(encoding="utf-8")
    assert "STORY BIBLE BODY" in result.story_bible.read_text(encoding="utf-8")
    assert result.series_bible == root / book_state.SERIES_BIBLE
    assert "STORY BIBLE BODY" in result.series_bible.read_text(encoding="utf-8")

    calls_after_first = client.digest_calls
    second = prescan_book.run_prescan(book, client, workers=2, series_root=root)
    assert client.digest_calls == calls_after_first
    assert all(entry["generated"] is False for entry in second.digests)
