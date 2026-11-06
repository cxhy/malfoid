"""Whole-book v2 translation entry tests (offline)."""

from __future__ import annotations

import json
import re
from pathlib import Path

import book_state
import segment_state
import translate_book_v2

SOURCE = """# Chapter 1: Start

One paragraph.

Two paragraph.

# Chapter 2: End

Three paragraph.
"""


class FakeClient:
    def __init__(self, fail: bool = False) -> None:
        self.calls: list[str] = []
        self.fail = fail

    def __call__(self, prompt: str) -> str:
        self.calls.append(prompt)
        count = len(re.findall(r"(?m)^\[(\d+)\]", prompt))
        if self.fail:
            return json.dumps({"complete": True, "targets": []}, ensure_ascii=False)
        targets = [f"译{i + 1}" for i in range(count)]
        return json.dumps({"complete": True, "targets": targets}, ensure_ascii=False)


def _book(tmp_path: Path) -> Path:
    root = tmp_path / "series"
    book_state.create_series(
        root,
        series_id="hp",
        title="Test",
        author="A",
        books=[{"number": 1, "title": "Book 1"}],
    )
    book = book_state.resolve_book(root, 1)
    source = tmp_path / "source.md"
    source.write_text(SOURCE, encoding="utf-8")
    segment_state.ingest_sources(book, [source], complete=True)
    return book


def test_translate_book_fills_all_pending_segments(tmp_path: Path) -> None:
    book = _book(tmp_path)
    client = FakeClient()
    summary = translate_book_v2.translate_book(book, client)
    assert summary["translated"] == 5
    assert summary["polished"] == 5
    chapter = segment_state.load_chapter(segment_state.chapter_path(book, 1))
    assert [s["target"] for s in chapter["segments"]] == ["译1", "译2", "译3"]
    chapter2 = segment_state.load_chapter(segment_state.chapter_path(book, 2))
    assert len(chapter2["segments"]) == 2
    assert all((s.get("target") or "").startswith("译") for s in chapter2["segments"])


def test_translate_book_rerun_makes_no_model_calls(tmp_path: Path) -> None:
    book = _book(tmp_path)
    client = FakeClient()
    translate_book_v2.translate_book(book, client)
    calls_after_first = len(client.calls)
    summary = translate_book_v2.translate_book(book, client)
    assert summary["translated"] == 0
    assert summary["polished"] == 0
    assert len(client.calls) == calls_after_first


def test_translate_book_without_polish(tmp_path: Path) -> None:
    book = _book(tmp_path)
    client = FakeClient()
    summary = translate_book_v2.translate_book(book, client, polish=False)
    assert summary["translated"] == 5
    assert summary["polished"] == 0
    chapter = segment_state.load_chapter(segment_state.chapter_path(book, 1))
    assert all((s.get("target") or "").strip() for s in chapter["segments"])


def test_translate_book_records_failures(tmp_path: Path) -> None:
    book = _book(tmp_path)
    summary = translate_book_v2.translate_book(book, FakeClient(fail=True), max_retries=0)
    assert summary["translated"] == 0
    assert summary["failures"]


def test_translate_book_injects_profile_rules(tmp_path: Path) -> None:
    book = _book(tmp_path)
    client = FakeClient()
    translate_book_v2.translate_book(book, client)
    assert any("哈利波特同人翻译约定" in prompt for prompt in client.calls)
    assert any("哈利波特同人润色约定" in prompt for prompt in client.calls)
