"""v2 book ingestion entry tests (offline)."""

from __future__ import annotations

import json
from pathlib import Path

import book_state
import ingest_book_v2


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
