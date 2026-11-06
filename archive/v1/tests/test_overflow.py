"""Overflow handling, glossary subsetting and resumable-artifact tests."""

from __future__ import annotations

import json
from pathlib import Path

import polish_all
import polish_chunk
import proofread_all
import proofread_chunk
import review_book
import term_filter


def test_term_filter_matches_whole_words_and_aliases() -> None:
    terms = [
        {"source": "Ron", "target": "罗恩", "aliases": ["Ronald Weasley"]},
        {"source": "Harry", "target": "哈利", "aliases": ["Harry Potter"]},
        {"source": "Draco", "target": "德拉科妮娅", "aliases": ["Darcy"]},
    ]
    selected = term_filter.select_terms(terms, "Darcy and Ron met Harry.")
    assert [t["source"] for t in selected] == ["Ron", "Harry", "Draco"]


def test_term_filter_does_not_match_ron_inside_word() -> None:
    terms = [{"source": "Ron", "target": "罗恩", "aliases": []}]
    assert term_filter.select_terms(terms, "The apron was green.") == []


def test_term_filter_check_target_for_chinese_output() -> None:
    terms = [
        {"source": "Hogwarts", "target": "霍格沃茨", "aliases": []},
        {"source": "Hogsmeade", "target": "霍格莫德", "aliases": []},
    ]
    selected = term_filter.select_terms(terms, "霍格沃茨的灯亮着。", check_target=True)
    assert [t["target"] for t in selected] == ["霍格沃茨"]


def test_overflow_detection_includes_full_completion_budget() -> None:
    usage = {
        "finish_reason": "stop",
        "completion_tokens": 32768,
        "completion_tokens_details": {"reasoning_tokens": 0},
    }
    assert polish_chunk.reasoning_overflow(usage, 32768)
    assert proofread_chunk.reasoning_overflow(usage, 32768)
    assert review_book.overflowed(usage, 32768)


def test_overflow_detection_normal_output() -> None:
    usage = {
        "finish_reason": "stop",
        "completion_tokens": 1200,
        "completion_tokens_details": {"reasoning_tokens": 300},
    }
    assert not polish_chunk.reasoning_overflow(usage, 32768)
    assert not proofread_chunk.reasoning_overflow(usage, 32768)
    assert not review_book.overflowed(usage, 32768)


def test_polish_all_all_chunks_and_valid(tmp_path: Path) -> None:
    work = tmp_path / "work"
    temp = work / "work_temp"
    polished = work / "translation" / "polished"
    temp.mkdir(parents=True)
    polished.mkdir(parents=True)
    (temp / "output_chunk0001.md").write_text("# 标题\n\n" + "中文" * 100, encoding="utf-8")
    (temp / "glossary.json").write_text(
        json.dumps({"terms": [{"source": "Darcy", "target": "德拉科妮娅", "aliases": []}]}),
        encoding="utf-8",
    )
    (polished / "chunk0001.md").write_text("# 标题\n\n" + "中文" * 100, encoding="utf-8")
    assert polish_all.all_chunks(work) == ["chunk0001"]
    assert polish_all.valid(work, "chunk0001")
    (polished / "chunk0001.md").write_text("# 标题\n\n达西。", encoding="utf-8")
    assert not polish_all.valid(work, "chunk0001")


def test_proofread_all_pending_ignores_unknown_reports(tmp_path: Path) -> None:
    work = tmp_path / "work"
    temp = work / "work_temp"
    reports = work / "translation" / "proofread"
    temp.mkdir(parents=True)
    reports.mkdir(parents=True)
    (temp / "chunk0001.md").write_text("x", encoding="utf-8")
    (temp / "chunk0002.md").write_text("x", encoding="utf-8")
    (reports / "chunk0001.md").write_text("## 严重\n\n无\n\n结论：PASS\n", encoding="utf-8")
    (reports / "chunk0002.md").write_text("## 严重\n\n无\n", encoding="utf-8")
    assert proofread_all.pending(work) == ["chunk0002"]


def test_polish_all_recheck_rejects_broken_output(tmp_path: Path) -> None:
    work = tmp_path / "work"
    temp = work / "work_temp"
    polished = work / "translation" / "polished"
    temp.mkdir(parents=True)
    polished.mkdir(parents=True)
    (temp / "output_chunk0001.md").write_text("# 标题\n\n" + "中文" * 100, encoding="utf-8")
    (temp / "glossary.json").write_text(
        json.dumps({"terms": [{"source": "Darcy", "target": "德拉科妮娅", "aliases": []}]}),
        encoding="utf-8",
    )
    (polished / "chunk0001.md").write_text("# 标题\n\n达西。", encoding="utf-8")
    assert not polish_all.valid(work, "chunk0001")
