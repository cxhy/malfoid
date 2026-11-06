"""Source ingestion, segment state and Markdown rendering tests (ticket 02)."""

from __future__ import annotations

import json
from pathlib import Path

import book_state
import segment_state

MARKDOWN_SOURCE = """# Chapter 1: Start

First paragraph with *emphasis*.

Second **paragraph**.

# Chapter 2: End

Final paragraph.
"""

MARKER_SOURCE = """=== Book 1: Chapter 5-7 ===

First paragraph.

Second paragraph.

=== Book 1: Chapters 8-12 ===

Third paragraph.
"""


def _book(tmp_path: Path) -> Path:
    root = tmp_path / "series"
    book_state.create_series(
        root,
        series_id="hp-tsundere",
        title="Tsundere",
        author="GuardBro",
        books=[{"number": 1, "title": "Book 1"}],
    )
    return book_state.resolve_book(root, 1)


def test_markdown_source_becomes_chapters_and_segments(tmp_path: Path) -> None:
    source = tmp_path / "book.md"
    source.write_text(MARKDOWN_SOURCE, encoding="utf-8")
    chapters = segment_state.read_source(source)
    assert [c["title"] for c in chapters] == ["Chapter 1: Start", "Chapter 2: End"]
    assert [c["chapter"] for c in chapters] == [1, 2]
    first = chapters[0]
    assert [s["kind"] for s in first["segments"]] == ["heading", "text", "text"]
    assert first["segments"][0]["source"] == "Chapter 1: Start"
    assert first["segments"][0]["level"] == 1
    assert first["segments"][1]["source"] == "First paragraph with *emphasis*."
    assert first["segments"][2]["source"] == "Second **paragraph**."
    assert [s["index"] for s in first["segments"]] == [0, 1, 2]


def test_wattpad_marker_source_becomes_chapters(tmp_path: Path) -> None:
    source = tmp_path / "book.txt"
    source.write_text(MARKER_SOURCE, encoding="utf-8")
    chapters = segment_state.read_source(source)
    assert [c["title"] for c in chapters] == [
        "Book 1: Chapter 5-7",
        "Book 1: Chapters 8-12",
    ]
    assert chapters[0]["segments"][0]["kind"] == "heading"
    assert chapters[0]["segments"][0]["level"] == 1
    assert [s["source"] for s in chapters[0]["segments"][1:]] == [
        "First paragraph.",
        "Second paragraph.",
    ]


def test_source_without_headings_is_one_chapter(tmp_path: Path) -> None:
    source = tmp_path / "notes.txt"
    source.write_text("One paragraph.\n\nTwo paragraph.\n", encoding="utf-8")
    chapters = segment_state.read_source(source)
    assert len(chapters) == 1
    assert chapters[0]["title"] == "notes"
    assert [s["kind"] for s in chapters[0]["segments"]] == ["text", "text"]


def test_text_before_the_first_heading_is_its_own_chapter(tmp_path: Path) -> None:
    source = tmp_path / "book.md"
    source.write_text("Intro paragraph.\n\n# Chapter 1: Start\n\nBody.\n", encoding="utf-8")
    chapters = segment_state.read_source(source)
    assert [c["title"] for c in chapters] == ["book", "Chapter 1: Start"]
    assert [s["kind"] for s in chapters[0]["segments"]] == ["text"]
    assert chapters[1]["segments"][0]["kind"] == "heading"


def test_every_heading_starts_a_chapter(tmp_path: Path) -> None:
    source = tmp_path / "book.md"
    source.write_text(
        "# Chapter 1: Start\n\n## Scene One\n\nText.\n\n# Chapter 2: End\n\nMore.\n",
        encoding="utf-8",
    )
    chapters = segment_state.read_source(source)
    assert [c["title"] for c in chapters] == [
        "Chapter 1: Start",
        "Scene One",
        "Chapter 2: End",
    ]
    assert chapters[0]["segments"][0]["level"] == 1
    assert chapters[1]["segments"][0]["level"] == 2
    assert chapters[1]["segments"][1]["source"] == "Text."


def test_segment_hashes_are_stable_and_content_addressed(tmp_path: Path) -> None:
    source = tmp_path / "book.md"
    source.write_text(MARKDOWN_SOURCE, encoding="utf-8")
    first = segment_state.read_source(source)
    second = segment_state.read_source(source)
    assert [s["source_hash"] for s in first[0]["segments"]] == [
        s["source_hash"] for s in second[0]["segments"]
    ]
    assert segment_state.source_hash("abc") == segment_state.source_hash("abc")
    assert segment_state.source_hash("abc") != segment_state.source_hash("abd")
    assert len(segment_state.source_hash("abc")) == 64


def test_chapter_state_round_trips_through_json(tmp_path: Path) -> None:
    source = tmp_path / "book.md"
    source.write_text(MARKDOWN_SOURCE, encoding="utf-8")
    chapter = segment_state.read_source(source)[0]
    path = segment_state.chapter_path(tmp_path / "book", 1)
    segment_state.save_chapter(path, chapter)
    loaded = segment_state.load_chapter(path)
    assert loaded == chapter


def test_render_markdown_reproduces_headings_and_paragraphs(tmp_path: Path) -> None:
    source = tmp_path / "book.md"
    source.write_text(MARKDOWN_SOURCE, encoding="utf-8")
    chapters = segment_state.read_source(source)
    rendered = segment_state.render_markdown(chapters[0])
    assert rendered == (
        "# Chapter 1: Start\n\nFirst paragraph with *emphasis*.\n\nSecond **paragraph**."
    )


def test_verify_round_trip_accepts_and_rejects(tmp_path: Path) -> None:
    source = tmp_path / "book.md"
    source.write_text(MARKDOWN_SOURCE, encoding="utf-8")
    chapter = segment_state.read_source(source)[0]
    assert segment_state.verify_round_trip(chapter) is True
    chapter["segments"].append(
        {
            "index": 3,
            "kind": "heading",
            "source": "Extra",
            "source_hash": segment_state.source_hash("Extra"),
            "target": None,
            "level": 1,
        }
    )
    assert segment_state.verify_round_trip(chapter) is False


def test_ingest_source_writes_chapter_state_work_md_and_meta(tmp_path: Path) -> None:
    book = _book(tmp_path)
    source = tmp_path / "book.txt"
    source.write_text(MARKER_SOURCE, encoding="utf-8")
    paths = segment_state.ingest_source(book, source)
    assert [p.name for p in paths] == ["ch-01.json", "ch-02.json"]
    assert (book / "source" / "chapters" / "ch-01.json").is_file()
    assert (book / "source" / "work.md").is_file()
    meta = json.loads((book / "meta.json").read_text(encoding="utf-8"))
    assert meta["chapter_count"] == 2
    assert [c["index"] for c in meta["chapters"]] == [1, 2]
    assert meta["chapters"][0]["title"] == "Book 1: Chapter 5-7"
    assert meta["source_files"] == [str(source)]


def test_reingest_does_not_delete_unrelated_chapter_state(tmp_path: Path) -> None:
    book = _book(tmp_path)
    extra = segment_state.chapter_path(book, 99)
    segment_state.save_chapter(
        extra,
        {"chapter": 99, "title": "Extra", "heading_level": 1, "segments": []},
    )
    source = tmp_path / "book.txt"
    source.write_text(MARKER_SOURCE, encoding="utf-8")
    segment_state.ingest_source(book, source)
    assert extra.is_file()


def test_render_book_orders_chapters_numerically(tmp_path: Path) -> None:
    book = _book(tmp_path)
    for number, title in ((99, "Ninety-nine"), (100, "One hundred")):
        segment_state.save_chapter(
            segment_state.chapter_path(book, number),
            {
                "chapter": number,
                "title": title,
                "heading_level": 1,
                "segments": [
                    {
                        "index": 0,
                        "kind": "heading",
                        "source": title,
                        "source_hash": segment_state.source_hash(title),
                        "target": None,
                        "level": 1,
                    }
                ],
            },
        )
    rendered = segment_state.render_book(book)
    assert rendered.index("Ninety-nine") < rendered.index("One hundred")


def test_render_book_joins_chapters_in_order(tmp_path: Path) -> None:
    book = _book(tmp_path)
    source = tmp_path / "book.txt"
    source.write_text(MARKER_SOURCE, encoding="utf-8")
    segment_state.ingest_source(book, source)
    rendered = segment_state.render_book(book)
    assert rendered.startswith("# Book 1: Chapter 5-7\n\nFirst paragraph.")
    assert "# Book 1: Chapters 8-12" in rendered
    assert rendered.index("First paragraph.") < rendered.index("Third paragraph.")


def test_render_markdown_translated_mode_uses_targets_and_falls_back_to_source() -> None:
    chapter = {
        "chapter": 1,
        "title": "Chapter 1",
        "heading_level": 1,
        "segments": [
            {
                "index": 0,
                "kind": "heading",
                "source": "Chapter 1",
                "source_hash": segment_state.source_hash("Chapter 1"),
                "target": "第一章",
                "level": 1,
            },
            {
                "index": 1,
                "kind": "text",
                "source": "First part.",
                "source_hash": segment_state.source_hash("First part."),
                "target": "第一段。",
            },
            {
                "index": 2,
                "kind": "text",
                "source": "Second part.",
                "source_hash": segment_state.source_hash("Second part."),
                "target": "第二段。",
                "cont": True,
            },
            {
                "index": 3,
                "kind": "text",
                "source": "Untranslated source.",
                "source_hash": segment_state.source_hash("Untranslated source."),
                "target": None,
            },
        ],
    }
    assert segment_state.render_markdown(chapter) == (
        "# Chapter 1\n\nFirst part.Second part.\n\nUntranslated source."
    )
    assert segment_state.render_markdown(chapter, translated=True) == (
        "# 第一章\n\n第一段。第二段。\n\nUntranslated source."
    )
