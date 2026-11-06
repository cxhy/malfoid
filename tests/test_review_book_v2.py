"""Generic v2 book review runner tests."""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path

import review_book_v2
import review_reports
import segment_state

_INDEX_RE = re.compile(r"^\[(\d+)\]$", re.MULTILINE)
_MESSAGE_RE = re.compile(r"^Message: (.*)$", re.MULTILINE)


def _segment(index: int, source: str, target: str, *, kind: str = "text") -> dict:
    return {
        "index": index,
        "kind": kind,
        "source": source,
        "source_hash": segment_state.source_hash(source),
        "target": target,
    }


def _chapter(number: int, segments: list[dict]) -> dict:
    return {
        "chapter": number,
        "title": f"Chapter {number}",
        "heading_level": 1,
        "segments": segments,
    }


def _write_book(tmp_path: Path, chapters: list[dict]) -> Path:
    book = tmp_path / "book"
    for chapter in chapters:
        segment_state.save_chapter(
            segment_state.chapter_path(book, int(chapter["chapter"])), chapter
        )
    return book


class BookReviewClient:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self._lock = threading.Lock()

    def __call__(self, prompt: str) -> str:
        with self._lock:
            self.calls.append(prompt)
        if prompt.startswith("TASK: REVIEW"):
            indices = [int(value) for value in _INDEX_RE.findall(prompt)]
            return json.dumps(
                {
                    "issues": [
                        {
                            "segment_index": indices[-1],
                            "message": "称呼漂移：跨章。",
                            "subject": "Draco",
                            "proposal": "统一称呼。",
                        }
                    ],
                    "reviewed_segments": len(indices),
                    "complete": True,
                },
                ensure_ascii=False,
            )
        if "TASK: VERIFY_REVIEW_ISSUE" in prompt:
            return json.dumps(
                {"verdict": "confirmed", "reason": "证据支持。", "requested_terms": []},
                ensure_ascii=False,
            )
        if "TASK: ARBITRATE_REVIEW_CONFLICT" in prompt:
            return json.dumps(
                {"decision": "unresolved", "reason": "留人工裁定。"}, ensure_ascii=False
            )
        raise AssertionError(f"unexpected prompt: {prompt[:80]}")


def test_review_book_reviews_whole_book_and_writes_reports(tmp_path: Path) -> None:
    chapters = [
        _chapter(
            1,
            [
                _segment(0, "Chapter One", "第一章", kind="heading"),
                _segment(1, "Draco waited.", "德拉科等待着。"),
            ],
        ),
        _chapter(
            2,
            [
                _segment(0, "Chapter Two", "第二章", kind="heading"),
                _segment(1, "Harry looked up.", "哈利抬起头。"),
            ],
        ),
    ]
    book = _write_book(tmp_path, chapters)
    before = segment_state.load_chapter(segment_state.chapter_path(book, 1))
    client = BookReviewClient()

    result = review_book_v2.review_book(book, client)

    assert result["advisory"] is True
    assert len(result["blocks"]) == 1
    assert [segment["index"] for segment in result["blocks"][0]] == [0, 1, 2, 3]
    candidates = result["evidence"]["candidates"]
    assert len(candidates) == 1
    candidate = candidates[0]["candidate"]
    assert (candidate["chapter"], candidate["segment_index"]) == (2, 1)
    assert candidate["global_segment_index"] == 3
    assert result["untraceable_issues"] == []

    review_dir = review_reports.review_dir(book)
    assert (review_dir / "block-001.json").is_file()
    assert (review_dir / "summary.json").is_file()
    block = json.loads((review_dir / "block-001.json").read_text(encoding="utf-8"))
    assert block["issues"][0]["chapter"] == 2
    assert block["issues"][0]["segment_index"] == 1
    assert block["issues"][0]["global_segment_index"] == 3
    assert block["evidence"][0]["candidate"]["chapter"] == 2
    assert block["evidence"][0]["candidate"]["segment_index"] == 1

    after = segment_state.load_chapter(segment_state.chapter_path(book, 1))
    assert after == before


def test_review_book_rejects_a_book_without_chapters(tmp_path: Path) -> None:
    book = tmp_path / "empty"
    book.mkdir()
    try:
        review_book_v2.review_book(book, BookReviewClient())
    except ValueError as exc:
        assert "chapter" in str(exc)
    else:
        raise AssertionError("expected a ValueError")


def test_flatten_segments_assigns_global_indices_and_local_positions() -> None:
    chapters = [
        _chapter(1, [_segment(0, "One.", "一。"), _segment(1, "Two.", "二。")]),
        _chapter(2, [_segment(0, "Three.", "三。")]),
    ]
    segments, global_map = review_book_v2.flatten_segments(chapters)
    assert [segment["index"] for segment in segments] == [0, 1, 2]
    assert [segment["chapter"] for segment in segments] == [1, 1, 2]
    assert [segment["local_index"] for segment in segments] == [0, 1, 0]
    assert global_map == {0: (1, 0), 1: (1, 1), 2: (2, 0)}
