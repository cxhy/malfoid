"""Re-polish tool tests (offline)."""

from __future__ import annotations

import json
from pathlib import Path

import book_state
import repolish_book_v2
import segment_state


class FakeClient:
    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if not self.responses:
            raise AssertionError("unexpected model call")
        return self.responses.pop(0)


def _book(tmp_path: Path) -> Path:
    root = tmp_path / "series"
    book_state.create_series(
        root,
        series_id="test",
        title="Test",
        author="A",
        books=[{"number": 1, "title": "Book 1"}],
    )
    book = book_state.resolve_book(root, 1)
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
            {
                "index": 1,
                "kind": "text",
                "source": "One.",
                "source_hash": segment_state.source_hash("One."),
                "target": "甲\n乙",
            },
            {
                "index": 2,
                "kind": "text",
                "source": "Two.",
                "source_hash": segment_state.source_hash("Two."),
                "target": "丙",
            },
        ],
    }
    segment_state.save_chapter(segment_state.chapter_path(book, 1), chapter)
    return book


def _payload(*targets: str) -> str:
    return json.dumps({"complete": True, "targets": list(targets)}, ensure_ascii=False)


def test_repolish_changes_text_and_keeps_heading(tmp_path: Path) -> None:
    book = _book(tmp_path)
    client = FakeClient([_payload("甲改\n乙", "丙改")])
    summary = repolish_book_v2.repolish_book(book, client)
    assert summary["polished"] == 2
    assert summary["failures"] == 0
    chapter = segment_state.load_chapter(segment_state.chapter_path(book, 1))
    assert chapter["segments"][0]["target"] == "第一章"
    assert chapter["segments"][1]["target"] == "甲改\n乙"
    assert chapter["segments"][2]["target"] == "丙改"
    assert "SOURCE:" in client.prompts[0]
    assert Path(summary["backup_dir"]).is_dir()
    backup = Path(summary["backup_dir"]) / "ch-01.json"
    assert json.loads(backup.read_text(encoding="utf-8"))["segments"][1]["target"] == "甲\n乙"


def test_repolish_failure_keeps_original_target(tmp_path: Path) -> None:
    book = _book(tmp_path)
    client = FakeClient([_payload("only-one")])
    summary = repolish_book_v2.repolish_book(book, client, max_retries=0)
    assert summary["polished"] == 0
    assert summary["failures"] == 2
    chapter = segment_state.load_chapter(segment_state.chapter_path(book, 1))
    assert chapter["segments"][1]["target"] == "甲\n乙"
    assert chapter["segments"][2]["target"] == "丙"


def test_repolish_dry_run_makes_no_calls_or_writes(tmp_path: Path) -> None:
    book = _book(tmp_path)
    client = FakeClient([])
    summary = repolish_book_v2.repolish_book(book, client, dry_run=True)
    assert summary["segments"] == 2
    assert client.prompts == []
    assert not (book / "archive").exists()


def test_repolish_dry_run_skips_separator(tmp_path: Path) -> None:
    book = _book(tmp_path)
    path = segment_state.chapter_path(book, 1)
    chapter = segment_state.load_chapter(path)
    chapter["segments"].append(
        {
            "index": 3,
            "kind": "separator",
            "source": "---",
            "source_hash": segment_state.source_hash("---"),
            "target": "---",
        }
    )
    segment_state.save_chapter(path, chapter)
    summary = repolish_book_v2.repolish_book(book, FakeClient([]), dry_run=True)
    assert summary["segments"] == 2
