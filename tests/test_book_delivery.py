"""Generic v2 book delivery tests."""

from __future__ import annotations

import json
from pathlib import Path

import book_delivery
import pytest
import segment_state
import verify_book


def _segment(index: int, source: str, target: str | None, *, kind: str = "text") -> dict:
    return {
        "index": index,
        "kind": kind,
        "source": source,
        "source_hash": segment_state.source_hash(source),
        "target": target,
    }


def _write_book(tmp_path: Path) -> Path:
    book = tmp_path / "book"
    meta = {"title": "Book One", "author": "Test Author"}
    (book / "meta.json").parent.mkdir(parents=True, exist_ok=True)
    (book / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    chapters = [
        {
            "chapter": 1,
            "title": "Chapter One",
            "heading_level": 1,
            "segments": [
                _segment(0, "Chapter One", "第一章", kind="heading"),
                _segment(1, "Draconia waited.", "德拉科妮娅等待着。"),
            ],
        },
        {
            "chapter": 2,
            "title": "Chapter Two",
            "heading_level": 1,
            "segments": [
                _segment(0, "Chapter Two", "第二章", kind="heading"),
                _segment(1, "Harry looked up.", "哈利抬起头。"),
            ],
        },
    ]
    for chapter in chapters:
        segment_state.save_chapter(
            segment_state.chapter_path(book, int(chapter["chapter"])), chapter
        )
    return book


def test_write_delivery_writes_translated_markdown_and_template_html(tmp_path: Path) -> None:
    book = _write_book(tmp_path)

    result = book_delivery.write_delivery(book)

    assert result.title == "Book One"
    assert result.markdown == book / "translation" / "Book One.md"
    assert result.html == book / "translation" / "Book One.html"
    assert result.markdown.is_file()
    assert result.html.is_file()

    markdown = result.markdown.read_text(encoding="utf-8")
    assert markdown.startswith("# 第一章")
    assert "德拉科妮娅等待着。" in markdown
    assert "哈利抬起头。" in markdown
    assert "Draconia waited." not in markdown

    html = result.html.read_text(encoding="utf-8")
    assert "FangSong" in html
    assert "toc-toggle" in html
    assert "目录" in html
    assert "德拉科妮娅等待着。" in html
    assert "哈利抬起头。" in html
    assert 'content="Test Author"' in html


def test_write_delivery_accepts_title_and_author_overrides(tmp_path: Path) -> None:
    book = _write_book(tmp_path)

    result = book_delivery.write_delivery(book, title="Override/Title", author="Other")

    assert result.markdown == book / "translation" / "Override-Title.md"
    assert result.html == book / "translation" / "Override-Title.html"
    meta = json.loads((book / "meta.json").read_text(encoding="utf-8"))
    assert meta["title"] == "Override/Title"
    assert meta["author"] == "Other"


def test_safe_title_removes_path_separators() -> None:
    assert book_delivery.safe_title("A/B\\C") == "A-B-C"
    assert book_delivery.safe_title("   ") == "book"


def _write_book_with_sized_chapters(tmp_path: Path, sizes: list[int]) -> Path:
    book = tmp_path / "book"
    meta = {"title": "Book One", "author": "Test Author"}
    (book / "meta.json").parent.mkdir(parents=True, exist_ok=True)
    (book / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    for number, size in enumerate(sizes, start=1):
        chapter = {
            "chapter": number,
            "title": f"Chapter {number}",
            "heading_level": 1,
            "segments": [
                _segment(0, f"Chapter {number}", f"第{number}章", kind="heading"),
                _segment(1, f"Source {number}", "字" * size),
            ],
        }
        segment_state.save_chapter(segment_state.chapter_path(book, number), chapter)
    return book


def test_write_delivery_splits_markdown_at_chapter_boundaries(tmp_path: Path) -> None:
    book = _write_book_with_sized_chapters(tmp_path, [40, 40, 40, 40])
    result = book_delivery.write_delivery(book, split_chars=100)
    assert result.markdown.is_file()
    assert result.html.is_file()
    assert len(result.markdown_parts) >= 2
    for index, path in enumerate(result.markdown_parts, start=1):
        assert path == book / "translation" / f"Book One - Part {index}.md"
        assert path.is_file()
        assert len(path.read_text(encoding="utf-8")) <= 100
    assert "字" * 40 in result.markdown.read_text(encoding="utf-8")


def test_write_delivery_keeps_single_markdown_when_under_limit(tmp_path: Path) -> None:
    book = _write_book_with_sized_chapters(tmp_path, [10, 10])
    result = book_delivery.write_delivery(book, split_chars=1000)
    assert result.markdown_parts == []


def test_split_markdown_balances_chapter_boundaries() -> None:
    markdown = "\n\n".join(f"# 第{i}章\n\n{'字' * 10}" for i in range(1, 5)) + "\n"
    parts = book_delivery.split_markdown(markdown, 40)
    assert len(parts) == 2
    assert [len(part) for part in parts] == [37, 37]


def test_split_markdown_rejects_a_single_oversized_chapter() -> None:
    markdown = "# 第一章\n\n" + "字" * 50 + "\n"
    try:
        book_delivery.split_markdown(markdown, 20)
    except ValueError as exc:
        assert "chapter" in str(exc)
    else:
        raise AssertionError("oversized chapter should be rejected")


def test_write_delivery_rejects_forbidden_names(tmp_path: Path) -> None:
    book = _write_book(tmp_path)
    path = segment_state.chapter_path(book, 1)
    chapter = segment_state.load_chapter(path)
    chapter["segments"][1]["target"] = "德拉科等待着。"
    segment_state.save_chapter(path, chapter)
    with pytest.raises(verify_book.DeliveryVerificationError):
        book_delivery.write_delivery(book)


def test_write_delivery_can_skip_verification(tmp_path: Path) -> None:
    book = _write_book(tmp_path)
    path = segment_state.chapter_path(book, 1)
    chapter = segment_state.load_chapter(path)
    chapter["segments"][1]["target"] = "德拉科等待着。"
    segment_state.save_chapter(path, chapter)
    result = book_delivery.write_delivery(book, verify=False)
    assert result.markdown.is_file()
