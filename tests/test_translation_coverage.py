"""Deterministic translation coverage tests."""

from __future__ import annotations

from pathlib import Path

import segment_state
import translation_coverage


def _segment(index: int, source: str, target: str) -> dict:
    return {
        "index": index,
        "kind": "text",
        "source": source,
        "source_hash": segment_state.source_hash(source),
        "target": target,
    }


def _book(tmp_path: Path, segments: list[dict]) -> Path:
    book = tmp_path / "book"
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
            },
            *segments,
        ],
    }
    segment_state.save_chapter(segment_state.chapter_path(book, 1), chapter)
    return book


def test_clean_book_has_no_coverage_issues(tmp_path: Path) -> None:
    book = _book(
        tmp_path,
        [
            _segment(1, "One sentence. Another sentence.", "一句。另一句。"),
            _segment(2, "Three sentences here. Four. Five.", "三句。四句。五句。"),
            _segment(3, "Six. Seven. Eight.", "六。七。八。"),
        ],
    )
    report = translation_coverage.coverage_report(book)
    assert report["errors"] == []
    assert report["warnings"] == []


def test_target_too_short_is_an_error(tmp_path: Path) -> None:
    book = _book(
        tmp_path,
        [
            _segment(1, "A" * 400, "甲" * 160),
            _segment(2, "B" * 400, "乙" * 160),
            _segment(3, "C" * 400, "丙" * 160),
            _segment(4, "D" * 400, "丁" * 40),
        ],
    )
    report = translation_coverage.coverage_report(book)
    assert any(item["kind"] == "target_too_short" for item in report["errors"])
    assert report["ok"] is False


def test_possible_missing_scene_tail_is_a_warning(tmp_path: Path) -> None:
    source = ("甲" * 200) + "\n---\n" + ("乙" * 100)
    book = _book(
        tmp_path,
        [_segment(1, source, "只翻译了前半段。" * 5)],
    )
    report = translation_coverage.coverage_report(book)
    assert any(
        item["kind"] == "possible_missing_scene_tail" for item in report["warnings"]
    )
    assert report["ok"] is True


def test_extra_scene_separator_is_a_warning(tmp_path: Path) -> None:
    book = _book(
        tmp_path,
        [_segment(1, "One scene.", "一个场景。\n---\n另一个场景。")],
    )
    report = translation_coverage.coverage_report(book)
    assert any(item["kind"] == "extra_scene_separator" for item in report["warnings"])


def test_scene_tail_sentence_density_suppresses_false_positive(tmp_path: Path) -> None:
    source = ("甲" * 200) + "\n---\n" + ("Tail sentence. " * 10)
    target = ("前半段。" * 5) + "\n" + ("尾段句子。" * 10)
    book = _book(tmp_path, [_segment(1, source, target)])
    report = translation_coverage.coverage_report(book)
    assert not any(
        item["kind"] == "possible_missing_scene_tail" for item in report["warnings"]
    )


def test_scene_tail_sentence_collapse_is_a_warning(tmp_path: Path) -> None:
    source = ("甲" * 200) + "\n---\n" + ("Tail sentence. " * 10)
    target = "只翻译了前半段。" * 5
    book = _book(tmp_path, [_segment(1, source, target)])
    report = translation_coverage.coverage_report(book)
    assert any(
        item["kind"] == "possible_missing_scene_tail" for item in report["warnings"]
    )
