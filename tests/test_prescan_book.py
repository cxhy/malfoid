"""Per-book prescan tests: parallel digests, hash caching and bible merge (ticket 07)."""

from __future__ import annotations

import re
import threading
from pathlib import Path

import book_state
import prescan_book
import pytest
import segment_state

SOURCE = """# 第一章 开端

德拉科妮娅在站台上等待。

哈利看了她一眼。

# 第二章 结尾

他们一起走进城堡。
"""

LONG_SOURCE = """# 第一章 开端

第一段。

# 第二章 中段

第二段。

# 第三章 结尾

第三段。
"""


def _series(tmp_path: Path) -> Path:
    root = tmp_path / "series"
    book_state.create_series(
        root,
        series_id="hp-tsundere",
        title="Harry Potter and the Sorcerer's Tsundere",
        author="GuardBro",
        books=[
            {"number": 1, "title": "Book 1"},
            {"number": 2, "title": "Book 2"},
        ],
    )
    return root


def _ingest(book: Path, tmp_path: Path, source: str = SOURCE) -> None:
    path = tmp_path / f"{book.name}-source.md"
    path.write_text(source, encoding="utf-8")
    segment_state.ingest_source(book, path)


def _chapter_title(prompt: str) -> str:
    match = re.search(r"^Chapter: (.+)$", prompt, re.MULTILINE)
    return match.group(1) if match else "unknown"


class FakeClient:
    """Offline model boundary with deterministic replies and call accounting."""

    def __init__(self, barrier: threading.Barrier | None = None) -> None:
        self.barrier = barrier
        self.calls: list[str] = []
        self.active = 0
        self.max_active = 0
        self.digest_seen = 0
        self._lock = threading.Lock()

    def __call__(self, prompt: str) -> str:
        with self._lock:
            self.calls.append(prompt)
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            if "TASK: DIGEST" in prompt:
                self.digest_seen += 1
                digest_number = self.digest_seen
            else:
                digest_number = 0
        try:
            if "TASK: DIGEST" in prompt:
                if self.barrier is not None and digest_number <= 2:
                    self.barrier.wait(timeout=2)
                return f"digest-of-{_chapter_title(prompt)}"
            if "TASK: STYLE_BRIEF" in prompt:
                return "STYLE BRIEF BODY"
            if "TASK: SYNOPSIS" in prompt:
                return "SYNOPSIS BODY"
            if "TASK: STORY_BIBLE" in prompt:
                return "STORY BIBLE BODY"
            return "unknown task"
        finally:
            with self._lock:
                self.active -= 1

    def digest_calls(self) -> int:
        return sum(1 for prompt in self.calls if "TASK: DIGEST" in prompt)


class CompleteClient:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return "object digest"


def test_should_prescan_requires_chapter_state_and_skips_preview(tmp_path: Path) -> None:
    root = _series(tmp_path)
    book = book_state.resolve_book(root, 1)
    assert prescan_book.should_prescan(book) is False
    _ingest(book, tmp_path)
    assert prescan_book.should_prescan(book) is True
    assert prescan_book.should_prescan(book, preview=True) is False


def test_run_prescan_refuses_an_incomplete_book(tmp_path: Path) -> None:
    root = _series(tmp_path)
    book = book_state.resolve_book(root, 1)
    client = FakeClient()
    with pytest.raises(prescan_book.PrescanNotReadyError):
        prescan_book.run_prescan(book, client)
    assert client.calls == []


def test_plan_digests_marks_uncached_chapters_then_cached(tmp_path: Path) -> None:
    root = _series(tmp_path)
    book = book_state.resolve_book(root, 1)
    _ingest(book, tmp_path)
    plan = prescan_book.plan_digests(book)
    assert [entry["chapter"] for entry in plan] == [1, 2]
    assert [entry["title"] for entry in plan] == ["第一章 开端", "第二章 结尾"]
    assert all(entry["cached"] is False for entry in plan)
    assert plan[0]["source_hash"] != plan[1]["source_hash"]
    assert all(str(entry["cache_path"]).startswith(str(book / "work_temp")) for entry in plan)

    prescan_book.run_digests(book, FakeClient())
    cached_plan = prescan_book.plan_digests(book)
    assert all(entry["cached"] is True for entry in cached_plan)


def test_digests_run_in_parallel_with_bounded_workers(tmp_path: Path) -> None:
    root = _series(tmp_path)
    book = book_state.resolve_book(root, 1)
    _ingest(book, tmp_path, LONG_SOURCE)
    barrier = threading.Barrier(2)
    client = FakeClient(barrier=barrier)
    digests = prescan_book.run_digests(book, client, workers=2)
    assert [entry["chapter"] for entry in digests] == [1, 2, 3]
    assert client.digest_calls() == 3
    assert client.max_active >= 2
    assert all(entry["digest"].startswith("digest-of-") for entry in digests)


def test_unchanged_chapters_reuse_cache_without_a_model_call(tmp_path: Path) -> None:
    root = _series(tmp_path)
    book = book_state.resolve_book(root, 1)
    _ingest(book, tmp_path)
    client = FakeClient()
    prescan_book.run_digests(book, client)
    calls_after_first = client.digest_calls()
    prescan_book.run_digests(book, client)
    assert client.digest_calls() == calls_after_first

    chapter_path = segment_state.chapter_path(book, 1)
    chapter = segment_state.load_chapter(chapter_path)
    chapter["segments"][1]["source"] = "德拉科妮娅改了台词。"
    chapter["segments"][1]["source_hash"] = segment_state.source_hash(
        chapter["segments"][1]["source"]
    )
    segment_state.save_chapter(chapter_path, chapter)

    plan = prescan_book.plan_digests(book)
    assert plan[0]["cached"] is False
    assert plan[1]["cached"] is True
    prescan_book.run_digests(book, client)
    assert client.digest_calls() == calls_after_first + 1


def test_run_prescan_writes_all_book_artifacts(tmp_path: Path) -> None:
    root = _series(tmp_path)
    book = book_state.resolve_book(root, 1)
    _ingest(book, tmp_path)
    client = FakeClient()
    result = prescan_book.run_prescan(book, client, workers=2)

    assert result.style_brief == prescan_book.style_brief_path(book)
    assert result.synopsis == prescan_book.synopsis_path(book)
    assert result.story_bible == prescan_book.story_bible_path(book)
    assert "STYLE BRIEF BODY" in result.style_brief.read_text(encoding="utf-8")
    assert "SYNOPSIS BODY" in result.synopsis.read_text(encoding="utf-8")
    assert "STORY BIBLE BODY" in result.story_bible.read_text(encoding="utf-8")
    assert [entry["chapter"] for entry in result.digests] == [1, 2]
    for entry in result.digests:
        assert entry["cache_path"].is_file()
    assert client.digest_calls() == 2
    assert len(client.calls) == 5


def test_run_prescan_merges_the_book_bible_into_the_series_bible(tmp_path: Path) -> None:
    root = _series(tmp_path)
    book = book_state.resolve_book(root, 1)
    _ingest(book, tmp_path)
    result = prescan_book.run_prescan(book, FakeClient(), series_root=root)

    series_bible = root / book_state.SERIES_BIBLE
    assert result.series_bible == series_bible
    text = series_bible.read_text(encoding="utf-8")
    assert text.startswith("# Series story bible")
    assert "STORY BIBLE BODY" in text
    assert "book-01:start" in text
    assert "book-01:end" in text


def test_merging_again_replaces_the_book_section_without_duplicates(tmp_path: Path) -> None:
    root = _series(tmp_path)
    book = book_state.resolve_book(root, 1)
    _ingest(book, tmp_path)
    prescan_book.run_prescan(book, FakeClient(), series_root=root)

    bible_path = prescan_book.story_bible_path(book)
    bible_path.write_text("# Story bible\n\nUPDATED BOOK BIBLE\n", encoding="utf-8")
    series_bible = prescan_book.merge_story_bible(root, book)

    text = series_bible.read_text(encoding="utf-8")
    assert "UPDATED BOOK BIBLE" in text
    assert "STORY BIBLE BODY" not in text
    assert text.count("book-01:start") == 1
    assert text.count("book-01:end") == 1


def test_series_bible_keeps_sections_from_multiple_books(tmp_path: Path) -> None:
    root = _series(tmp_path)
    book_one = book_state.resolve_book(root, 1)
    book_two = book_state.resolve_book(root, 2)
    _ingest(book_one, tmp_path)
    _ingest(book_two, tmp_path, "# 第一章 另一本\n\n另一段。\n")
    prescan_book.run_prescan(book_one, FakeClient(), series_root=root)
    prescan_book.run_prescan(book_two, FakeClient(), series_root=root)

    text = (root / book_state.SERIES_BIBLE).read_text(encoding="utf-8")
    assert "book-01:start" in text
    assert "book-02:start" in text
    assert text.count("STORY BIBLE BODY") == 2


def test_client_object_with_complete_is_accepted(tmp_path: Path) -> None:
    root = _series(tmp_path)
    book = book_state.resolve_book(root, 1)
    _ingest(book, tmp_path)
    client = CompleteClient()
    digests = prescan_book.run_digests(book, client, workers=1)
    assert [entry["digest"] for entry in digests] == ["object digest", "object digest"]
    assert len(client.prompts) == 2


def test_worker_count_must_be_positive(tmp_path: Path) -> None:
    root = _series(tmp_path)
    book = book_state.resolve_book(root, 1)
    _ingest(book, tmp_path)
    with pytest.raises(ValueError):
        prescan_book.run_digests(book, FakeClient(), workers=0)
