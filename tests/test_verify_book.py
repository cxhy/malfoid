"""Deterministic delivery verification tests."""

from __future__ import annotations

import json
from pathlib import Path

import segment_state
import verify_book


def _segment(index: int, source: str, target: str, *, kind: str = "text") -> dict:
    return {
        "index": index,
        "kind": kind,
        "source": source,
        "source_hash": segment_state.source_hash(source),
        "target": target,
    }


def _book(
    tmp_path: Path,
    *,
    heading: str = "第一章",
    text: str = "德拉科妮娅等待着。",
    title: str = "Chapter 1",
) -> Path:
    book = tmp_path / "book"
    (book / "meta.json").parent.mkdir(parents=True, exist_ok=True)
    (book / "meta.json").write_text("{}", encoding="utf-8")
    chapter = {
        "chapter": 1,
        "title": title,
        "heading_level": 1,
        "segments": [
            _segment(0, "Chapter 1", heading, kind="heading"),
            _segment(1, "Draconia waited.", text),
        ],
    }
    segment_state.save_chapter(segment_state.chapter_path(book, 1), chapter)
    return book


def _set_text(book: Path, text: str) -> None:
    path = segment_state.chapter_path(book, 1)
    chapter = segment_state.load_chapter(path)
    chapter["segments"][1]["target"] = text
    segment_state.save_chapter(path, chapter)


def test_verify_book_passes_a_clean_book(tmp_path: Path) -> None:
    report = verify_book.verify_book(_book(tmp_path))
    assert report["ok"] is True
    assert report["errors"] == []


def test_verify_book_reports_missing_targets(tmp_path: Path) -> None:
    book = _book(tmp_path)
    path = segment_state.chapter_path(book, 1)
    chapter = segment_state.load_chapter(path)
    chapter["segments"][1]["target"] = ""
    segment_state.save_chapter(path, chapter)
    report = verify_book.verify_book(book)
    assert report["ok"] is False
    assert any(item["kind"] == "missing_targets" for item in report["errors"])


def test_verify_book_rejects_bare_draco_and_english(tmp_path: Path) -> None:
    book = _book(tmp_path)
    _set_text(book, "德拉科等待着 Harry。")
    report = verify_book.verify_book(book)
    kinds = {item["kind"] for item in report["errors"]}
    assert "bare_draco" in kinds
    assert "english_residue" in kinds


def test_verify_book_reports_glossary_variant_drift(tmp_path: Path) -> None:
    book = _book(tmp_path, text="他靠快速咒语课程学魔法。")
    glossary = {
        "version": 2,
        "high_frequency_top_n": 20,
        "applied_meta_hashes": {},
        "terms": [
            {
                "id": "Kwikspell",
                "source": "Kwikspell",
                "target": "速成咒术课程",
                "aliases": [],
                "category": "item",
                "gender": "unknown",
                "confidence": "high",
                "frequency": 1,
                "evidence_refs": [],
                "notes": "",
                "forbidden_targets": ["快速咒语课程"],
            }
        ],
    }
    path = book / "work_temp" / "glossary.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(glossary, ensure_ascii=False), encoding="utf-8")
    report = verify_book.verify_book(book)
    assert report["ok"] is False
    assert any(item["kind"] == "glossary_variant" for item in report["errors"])


def test_verify_book_heading_format_is_a_warning_for_custom_titles(tmp_path: Path) -> None:
    report = verify_book.verify_book(
        _book(tmp_path, heading="第1章", title="Prologue")
    )
    assert report["ok"] is True
    assert any(item["kind"] == "heading_digits" for item in report["warnings"])


def test_verify_book_uses_the_deterministic_heading(tmp_path: Path) -> None:
    report = verify_book.verify_book(
        _book(tmp_path, heading="第1章-6", title="Ch1-6")
    )
    assert report["ok"] is True
    assert report["warnings"] == []


def test_verify_book_allows_the_canonical_foreign_inscription(tmp_path: Path) -> None:
    book = _book(
        tmp_path,
        text="铭文：“Erised stra ehru oyt ube cafru oyt on wohsi”。",
    )
    report = verify_book.verify_book(book)
    assert report["ok"] is True
    assert report["counts"]["english_words"] == 0
