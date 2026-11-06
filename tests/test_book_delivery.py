"""Generic v2 book delivery tests."""

from __future__ import annotations

import json
from pathlib import Path

import book_delivery
import segment_state


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
                _segment(1, "Draco waited.", "德拉科等待着。"),
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
    assert "德拉科等待着。" in markdown
    assert "哈利抬起头。" in markdown
    assert "Draco waited." not in markdown

    html = result.html.read_text(encoding="utf-8")
    assert "FangSong" in html
    assert "toc-toggle" in html
    assert "目录" in html
    assert "德拉科等待着。" in html
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
