"""v2 book ingestion entry tests (offline)."""

from __future__ import annotations

import json
from pathlib import Path

import book_state
import ingest_book_v2
import segment_state


def test_ingest_book_creates_series_book_and_chapters(tmp_path: Path) -> None:
    root = tmp_path / "series"
    source = tmp_path / "source.txt"
    source.write_text("=== Book 1: Chapter 5-7 ===\n\nOne.\n\nCh 6\n\nTwo.\n", encoding="utf-8")
    summary = ingest_book_v2.ingest_book(
        root,
        series_id="hp",
        title="Test",
        author="A",
        book_number=1,
        sources=[source],
        book_title="Book 1",
    )
    assert summary["chapter_count"] == 2
    assert summary["source_complete"] is True
    assert book_state.is_series_root(root)
    book = book_state.resolve_book(root, 1)
    meta = json.loads((book / "meta.json").read_text(encoding="utf-8"))
    assert meta["source_complete"] is True
    assert [c["index"] for c in meta["chapters"]] == [5, 6]


def test_ingest_book_can_mark_partial(tmp_path: Path) -> None:
    root = tmp_path / "series"
    source = tmp_path / "source.txt"
    source.write_text("=== Book 1: Chapter 5-7 ===\n\nOne.\n", encoding="utf-8")
    summary = ingest_book_v2.ingest_book(
        root,
        series_id="hp",
        title="Test",
        author="A",
        book_number=1,
        sources=[source],
        book_title="Book 1",
        complete=False,
    )
    assert summary["source_complete"] is False


def test_ingest_book_ignores_bare_book_label_before_first_chapter(tmp_path: Path) -> None:
    root = tmp_path / "series"
    source = tmp_path / "source.txt"
    source.write_text(
        "=== Book 2: Chapters 1-5 ===\n\nBook 2\n\nCh 1\n\nOne.\n\nCh 2\n\nTwo.\n",
        encoding="utf-8",
    )
    summary = ingest_book_v2.ingest_book(
        root,
        series_id="hp",
        title="Test",
        author="A",
        book_number=2,
        sources=[source],
        book_title="Book 2",
    )
    assert summary["chapter_count"] == 2
    book = book_state.resolve_book(root, 2)
    meta = json.loads((book / "meta.json").read_text(encoding="utf-8"))
    assert [c["index"] for c in meta["chapters"]] == [1, 2]


def test_ingest_book_supports_decimal_chapter_numbers(tmp_path: Path) -> None:
    root = tmp_path / "series"
    source = tmp_path / "source.txt"
    source.write_text(
        "=== Book 4: Chapters 23-23.5 ===\n\n"
        "Ch 23\n\nTwenty-three.\n\n"
        "Ch 23.5\n\nTwenty-three point five.\n\n"
        "Ch 37\n\nThirty-seven.\n\n"
        "Ch 37.5\n\nThirty-seven point five.\n",
        encoding="utf-8",
    )
    summary = ingest_book_v2.ingest_book(
        root,
        series_id="hp",
        title="Test",
        author="A",
        book_number=4,
        sources=[source],
        book_title="Book 4",
    )
    assert summary["chapter_count"] == 4
    book = book_state.resolve_book(root, 4)
    meta = json.loads((book / "meta.json").read_text(encoding="utf-8"))
    assert [c["index"] for c in meta["chapters"]] == [23, 23.5, 37, 37.5]
    names = sorted(p.name for p in (book / "source" / "chapters").glob("ch-*.json"))
    assert names == ["ch-23.5.json", "ch-23.json", "ch-37.5.json", "ch-37.json"]
    assert segment_state.render_book(book).startswith("# Ch 23")


def test_ingest_book_handles_combined_and_parenthetical_markers(tmp_path: Path) -> None:
    root = tmp_path / "series"
    source = tmp_path / "source.txt"
    source.write_text(
        "=== Book 4: Chapters 8-9 ===\n\n"
        "Ch 8\n\nEight.\n\n"
        "Ch 9 (Combination)\n\nNine.\n\n"
        "Ch 30 happens as it normally does, and this is prose.\n\n"
        "Ch 33+34\n\nThirty-three and thirty-four.\n",
        encoding="utf-8",
    )
    summary = ingest_book_v2.ingest_book(
        root,
        series_id="hp",
        title="Test",
        author="A",
        book_number=4,
        sources=[source],
        book_title="Book 4",
    )
    assert summary["chapter_count"] == 3
    book = book_state.resolve_book(root, 4)
    meta = json.loads((book / "meta.json").read_text(encoding="utf-8"))
    assert [c["index"] for c in meta["chapters"]] == [8, 9, 33]
    chapter = segment_state.load_chapter(segment_state.chapter_path(book, 9))
    texts = [seg["source"] for seg in chapter["segments"] if seg["kind"] == "text"]
    assert "Ch 30 happens as it normally does, and this is prose." in texts


def test_ingest_records_covered_chapters_and_uses_them_for_headings(tmp_path: Path) -> None:
    root = tmp_path / "series"
    source = tmp_path / "source.txt"
    source.write_text(
        "=== Book 3: Chapters 1-22 ===\n\n"
        "Ch 1-4\n\nOne.\n\n"
        "Ch18-19-20\n\nTwo.\n\n"
        "Ch 21-22\n\nThree.\n",
        encoding="utf-8",
    )
    summary = ingest_book_v2.ingest_book(
        root,
        series_id="hp",
        title="Test",
        author="A",
        book_number=3,
        sources=[source],
        book_title="Book 3",
    )
    assert summary["chapter_count"] == 3
    book = book_state.resolve_book(root, 3)
    meta = json.loads((book / "meta.json").read_text(encoding="utf-8"))
    assert [c["covered_chapters"] for c in meta["chapters"]] == [[1, 4], [18, 19, 20], [21, 22]]
    first = segment_state.load_chapter(segment_state.chapter_path(book, 1))
    assert first["covered_chapters"] == [1, 4]
    assert segment_state.display_chapter_heading(first) == "第一至四章"
