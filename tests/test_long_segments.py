"""Long-segment splitting and continuation merging tests (ticket 03)."""

from __future__ import annotations

from pathlib import Path

import segment_state

OVERSIZED = "Alpha one. Bravo two. Charlie three. Delta four. Echo five."
LONG_SOURCE = "# Chapter 1: Start\n\n" + OVERSIZED + "\n\nShort tail.\n"


def _segment(text: str, index: int = 0) -> dict:
    return {
        "index": index,
        "kind": "text",
        "source": text,
        "source_hash": segment_state.source_hash(text),
        "target": None,
    }


def test_long_paragraph_splits_at_sentence_boundaries(tmp_path: Path) -> None:
    source = tmp_path / "book.md"
    source.write_text(LONG_SOURCE, encoding="utf-8")
    chapter = segment_state.read_source(source, max_segment_tokens=6)[0]
    segments = chapter["segments"]
    parts = segments[1:4]

    assert [s["source"] for s in parts] == [
        "Alpha one. Bravo two. ",
        "Charlie three. ",
        "Delta four. Echo five.",
    ]
    assert [s["index"] for s in parts] == [1, 2, 3]
    assert parts[0]["part_number"] == 0
    assert "cont" not in parts[0]
    assert parts[1]["cont"] is True
    assert parts[2]["cont"] is True
    assert all(segment_state.estimate_tokens(s["source"]) <= 6 for s in parts)


def test_split_parts_keep_the_logical_segment_identity(tmp_path: Path) -> None:
    source = tmp_path / "book.md"
    source.write_text(LONG_SOURCE, encoding="utf-8")
    chapter = segment_state.read_source(source, max_segment_tokens=6)[0]
    segments = chapter["segments"]
    logical_hash = segment_state.source_hash(OVERSIZED)

    assert [s["logical_index"] for s in segments[1:4]] == [1, 1, 1]
    assert "logical_index" not in segments[0]
    assert "logical_index" not in segments[4]
    assert segments[1]["logical_source_hash"] == logical_hash
    assert segments[1]["part_number"] == 0
    assert all(s["logical_source_hash"] == logical_hash for s in segments[2:4])
    assert "".join(s["source"] for s in segments[1:4]) == OVERSIZED


def test_splitting_and_merging_do_not_change_the_source_hash(tmp_path: Path) -> None:
    source = tmp_path / "book.md"
    source.write_text(LONG_SOURCE, encoding="utf-8")
    chapter = segment_state.read_source(source, max_segment_tokens=6)[0]
    logical_hash = segment_state.source_hash(OVERSIZED)

    merged = "".join(s["source"] for s in chapter["segments"][1:4])
    assert segment_state.source_hash(merged) == logical_hash
    assert segment_state.render_markdown(chapter) == (
        "# Chapter 1: Start\n\n" + OVERSIZED + "\n\nShort tail."
    )


def test_render_merges_continuation_parts_into_one_paragraph(tmp_path: Path) -> None:
    source = tmp_path / "book.md"
    source.write_text(LONG_SOURCE, encoding="utf-8")
    chapter = segment_state.read_source(source, max_segment_tokens=6)[0]
    rendered = segment_state.render_markdown(chapter)

    assert OVERSIZED in rendered
    assert rendered.count("\n\n") == 2
    assert rendered.split("\n\n")[1] == OVERSIZED


def test_split_long_segment_uses_the_parent_index_and_hash() -> None:
    segment = _segment(OVERSIZED, index=3)
    parts = segment_state.split_long_segment(segment, max_segment_tokens=6)

    assert len(parts) == 3
    assert parts[0]["index"] == 3
    assert parts[1]["index"] == 4
    assert parts[2]["index"] == 5
    assert parts[0]["logical_source_hash"] == segment["source_hash"]
    assert parts[0]["logical_index"] == 3
    assert parts[1]["logical_index"] == 3
    assert parts[1]["logical_index"] == 3
    assert parts[1]["logical_source_hash"] == segment["source_hash"]
    assert parts[1]["cont"] is True
    assert parts[2]["cont"] is True


def test_short_paragraph_is_not_split(tmp_path: Path) -> None:
    source = tmp_path / "book.txt"
    source.write_text("Short one.\n\nShort two.\n", encoding="utf-8")
    chapter = segment_state.read_source(source, max_segment_tokens=6)[0]

    assert [s["source"] for s in chapter["segments"]] == ["Short one.", "Short two."]
    assert all("cont" not in s for s in chapter["segments"])


def test_short_source_keeps_the_existing_segment_shape(tmp_path: Path) -> None:
    source = tmp_path / "book.txt"
    source.write_text("Short one.\n", encoding="utf-8")
    chapter = segment_state.read_source(source)[0]

    assert chapter["segments"] == [
        {
            "index": 0,
            "kind": "text",
            "source": "Short one.",
            "source_hash": segment_state.source_hash("Short one."),
            "target": None,
        }
    ]


def test_default_budget_splits_an_oversized_paragraph(tmp_path: Path) -> None:
    paragraph = " ".join(
        f"Synthetic sentence {i} keeps the fixture deterministic." for i in range(120)
    )
    source = tmp_path / "book.txt"
    source.write_text(paragraph, encoding="utf-8")
    chapter = segment_state.read_source(source)[0]
    segments = chapter["segments"]

    assert segment_state.estimate_tokens(paragraph) > segment_state.DEFAULT_SEGMENT_TOKEN_BUDGET
    assert len(segments) > 1
    assert all(
        segment_state.estimate_tokens(s["source"]) <= segment_state.DEFAULT_SEGMENT_TOKEN_BUDGET
        for s in segments
    )
    assert segments[0]["logical_source_hash"] == segment_state.source_hash(paragraph)
    assert segment_state.render_markdown(chapter) == paragraph


def test_abbreviations_do_not_create_false_sentence_boundaries() -> None:
    text = "Mr. Smith wrote a short note. He slept."
    parts = segment_state.split_long_segment(_segment(text), max_segment_tokens=3)

    assert [p["source"] for p in parts] == [
        "Mr. Smith wrote a short note. ",
        "He slept.",
    ]
    assert segment_state.render_markdown({"segments": parts}) == text


def test_text_without_a_sentence_boundary_is_not_split() -> None:
    text = "Supercalifragilisticexpialidocious" * 10 + "."
    segment = _segment(text)
    parts = segment_state.split_long_segment(segment, max_segment_tokens=1)

    assert parts == [segment]
    assert segment_state.estimate_tokens(text) > 1


def test_split_segments_is_idempotent(tmp_path: Path) -> None:
    source = tmp_path / "book.md"
    source.write_text(LONG_SOURCE, encoding="utf-8")
    chapter = segment_state.read_source(source, max_segment_tokens=6)[0]

    again = segment_state.split_segments(chapter["segments"], max_segment_tokens=6)
    assert again == chapter["segments"]


def test_split_state_round_trips_and_renders(tmp_path: Path) -> None:
    source = tmp_path / "book.md"
    source.write_text(LONG_SOURCE, encoding="utf-8")
    chapter = segment_state.read_source(source, max_segment_tokens=6)[0]
    path = segment_state.chapter_path(tmp_path / "book", 1)
    segment_state.save_chapter(path, chapter)
    loaded = segment_state.load_chapter(path)

    assert loaded == chapter
    assert segment_state.render_markdown(loaded) == segment_state.render_markdown(chapter)


def test_ingest_writes_a_merged_work_md(tmp_path: Path) -> None:
    book = tmp_path / "book"
    source = tmp_path / "book.md"
    source.write_text(LONG_SOURCE, encoding="utf-8")
    segment_state.ingest_source(book, source, max_segment_tokens=6)

    rendered = (book / "source" / "work.md").read_text(encoding="utf-8")
    assert rendered == "# Chapter 1: Start\n\n" + OVERSIZED + "\n\nShort tail.\n"
    chapter = segment_state.load_chapter(segment_state.chapter_path(book, 1))
    assert len(chapter["segments"]) == 5


def test_verify_round_trip_accepts_a_split_chapter(tmp_path: Path) -> None:
    source = tmp_path / "book.md"
    source.write_text(LONG_SOURCE, encoding="utf-8")
    chapter = segment_state.read_source(source, max_segment_tokens=6)[0]

    assert segment_state.verify_round_trip(chapter, max_segment_tokens=6) is True
    assert segment_state.verify_round_trip(chapter) is True
