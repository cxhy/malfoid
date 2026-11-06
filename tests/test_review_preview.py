"""Preview review validation tests (ticket 11).

The fixtures are synthetic preview books. Review, evidence verification and
arbitration all go through an injected fake client, so the suite never touches a
live model endpoint.
"""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path

import preview_migration
import pytest
import review_preview
import review_reports
import segment_state

_REVIEW_INDEX_RE = re.compile(r"^\[(\d+)\]$", re.MULTILINE)
_MESSAGE_RE = re.compile(r"^Message: (.*)$", re.MULTILINE)


def _segment(
    index: int,
    source: str,
    target: str | None,
    *,
    kind: str = "text",
    source_hash: str | None = None,
    target_origin: str | None = None,
) -> dict:
    segment = {
        "index": index,
        "kind": kind,
        "source": source,
        "source_hash": source_hash or segment_state.source_hash(source),
        "target": target,
    }
    if target_origin is not None:
        segment["target_origin"] = target_origin
    return segment


def _chapter(number: int, segments: list[dict]) -> dict:
    return {
        "chapter": number,
        "title": f"Chapter {number}",
        "heading_level": 1,
        "segments": segments,
    }


def _write_book(tmp_path: Path, chapters: list[dict]) -> Path:
    book = tmp_path / "preview"
    for chapter in chapters:
        path = segment_state.chapter_path(book, int(chapter["chapter"]))
        segment_state.save_chapter(path, chapter)
    return book


class PreviewClient:
    """Offline client answering review, evidence and arbitration prompts."""

    def __init__(self, *, review_issues=None, verdicts=None) -> None:
        self.review_issues = review_issues
        self.verdicts = verdicts or {}
        self.calls: list[str] = []
        self._lock = threading.Lock()

    def __call__(self, prompt: str) -> str:
        with self._lock:
            self.calls.append(prompt)
        if prompt.startswith("TASK: REVIEW"):
            indices = [int(value) for value in _REVIEW_INDEX_RE.findall(prompt)]
            if self.review_issues is not None:
                issues = self.review_issues(indices)
            else:
                issues = [
                    {"segment_index": indices[-1], "message": "误报：实际一致。"},
                ]
                if len(indices) > 1:
                    issues.append(
                        {
                            "segment_index": indices[-2],
                            "message": "称呼漂移：称呼不一致。",
                        }
                    )
            return json.dumps(
                {
                    "issues": issues,
                    "reviewed_segments": len(indices),
                    "complete": True,
                },
                ensure_ascii=False,
            )
        if "TASK: VERIFY_REVIEW_ISSUE" in prompt:
            match = _MESSAGE_RE.search(prompt)
            message = match.group(1) if match else ""
            verdict = self.verdicts.get(message, "rejected")
            return json.dumps(
                {
                    "verdict": verdict,
                    "reason": "fake evidence",
                    "requested_terms": [],
                },
                ensure_ascii=False,
            )
        if "TASK: ARBITRATE_REVIEW_CONFLICT" in prompt:
            return json.dumps(
                {"decision": "unresolved", "reason": "fake arbitration"},
                ensure_ascii=False,
            )
        raise AssertionError(f"unexpected prompt: {prompt[:80]}")

    def call_count(self) -> int:
        return len(self.calls)


def test_check_alignment_accepts_aligned_source_target_pairs() -> None:
    chapter = _chapter(
        1,
        [
            _segment(0, "Chapter One", "第一章", kind="heading"),
            _segment(1, "Draco waited in the alley.", "德拉科在小巷里等待。"),
        ],
    )
    result = review_preview.check_alignment([chapter])
    assert result["clean"] is True
    assert result["checked"] == 2
    assert result["misaligned"] == []


def test_check_alignment_flags_missing_and_non_chinese_targets() -> None:
    chapter = _chapter(
        1,
        [
            _segment(0, "Chapter One", "第一章", kind="heading"),
            _segment(1, "Draco waited.", "德拉科等待着。"),
            _segment(2, "Harry looked up.", "Harry looked up."),
            _segment(3, "They left.", None),
        ],
    )
    result = review_preview.check_alignment([chapter])
    assert result["clean"] is False
    refs = {item["ref"] for item in result["misaligned"]}
    assert refs == {"ch-01:2", "ch-01:3"}
    assert [item["ref"] for item in result["non_chinese_targets"]] == ["ch-01:2"]
    assert [item["ref"] for item in result["missing_targets"]] == ["ch-01:3"]


def test_check_alignment_flags_a_source_hash_mismatch() -> None:
    chapter = _chapter(
        1,
        [
            _segment(0, "Draco waited.", "德拉科等待着。", source_hash="stale-hash"),
        ],
    )
    result = review_preview.check_alignment([chapter])
    assert result["clean"] is False
    assert [item["ref"] for item in result["hash_mismatches"]] == ["ch-01:0"]


def test_check_alignment_flags_a_reused_target_that_does_not_match_the_hash() -> None:
    source = "Draco waited."
    chapter = _chapter(
        1,
        [
            _segment(
                0,
                source,
                "错误译文。",
                target_origin="polished",
            )
        ],
    )
    reuse_map = {
        segment_state.source_hash(source): {"target": "正确译文。", "origin": "polished"},
    }
    result = review_preview.check_alignment([chapter], reuse_map=reuse_map)
    assert result["clean"] is False
    assert [item["ref"] for item in result["reuse_mismatches"]] == ["ch-01:0"]


def test_run_preview_review_writes_reports_metrics_and_go_decision(tmp_path: Path) -> None:
    chapter = _chapter(
        1,
        [
            _segment(0, "Chapter One", "第一章", kind="heading"),
            _segment(1, "Draco waited in the alley.", "德拉科在小巷里等待。"),
            _segment(2, "Harry looked up.", "哈利抬起头。"),
        ],
    )
    book = _write_book(tmp_path, [chapter])
    client = PreviewClient(
        verdicts={
            "称呼漂移：称呼不一致。": "confirmed",
            "误报：实际一致。": "rejected",
        }
    )

    result = review_preview.run_preview_review(book, client)

    assert result["metrics"]["issues"] == 2
    assert result["metrics"]["confirmed"] == 1
    assert result["metrics"]["rejected"] == 1
    assert result["metrics"]["false_positive_rate"] == 0.5
    assert result["metrics"]["alignment_false_positives"] == 0
    assert result["metrics"]["complete"] is True
    assert result["alignment"]["clean"] is True
    assert result["decision"]["decision"] == "GO"
    assert result["advisory"] is True

    review_dir = review_reports.review_dir(book)
    assert (review_dir / "block-001.json").is_file()
    assert (review_dir / "block-001.md").is_file()
    assert (review_dir / "summary.json").is_file()
    assert (review_dir / "summary.md").is_file()
    assert (review_dir / "acceptance.json").is_file()
    assert (review_dir / "acceptance.md").is_file()

    block = json.loads((review_dir / "block-001.json").read_text(encoding="utf-8"))
    assert [issue["segment_index"] for issue in block["issues"]] == [1, 2]
    assert [item["segment_index"] for item in block["recommendations"]] == [1]
    assert block["verdict"] == review_reports.FAIL

    acceptance = json.loads((review_dir / "acceptance.json").read_text(encoding="utf-8"))
    assert acceptance["advisory"] is True
    assert acceptance["decision"]["decision"] == "GO"
    assert acceptance["metrics"]["false_positive_rate"] == 0.5

    reloaded = segment_state.load_chapter(segment_state.chapter_path(book, 1))
    assert [segment["target"] for segment in reloaded["segments"]] == [
        "第一章",
        "德拉科在小巷里等待。",
        "哈利抬起头。",
    ]


def test_run_preview_review_drops_misaligned_issues_and_decides_no_go(tmp_path: Path) -> None:
    chapter = _chapter(
        1,
        [
            _segment(0, "Chapter One", "第一章", kind="heading"),
            _segment(1, "Draco waited.", "德拉科等待着。"),
            _segment(2, "Harry looked up.", "Harry looked up."),
        ],
    )
    book = _write_book(tmp_path, [chapter])
    client = PreviewClient(
        review_issues=lambda indices: [
            {"segment_index": 2, "message": "称呼漂移：误对齐。"},
        ]
    )

    result = review_preview.run_preview_review(book, client)

    assert result["metrics"]["issues"] == 1
    assert result["metrics"]["valid_issues"] == 0
    assert result["metrics"]["alignment_false_positives"] == 1
    assert result["metrics"]["alignment_false_positive_rate"] == 1.0
    assert result["metrics"]["false_positive_rate"] == 0.0
    assert result["alignment"]["clean"] is False
    assert result["decision"]["decision"] == "NO-GO"
    assert result["invalid_issues"][0]["reason"] == "misaligned anchor"

    review_dir = review_reports.review_dir(book)
    block = json.loads((review_dir / "block-001.json").read_text(encoding="utf-8"))
    assert block["issues"] == []
    assert block["recommendations"] == []


def test_run_preview_review_keeps_chapter_positions_apart(tmp_path: Path) -> None:
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
    client = PreviewClient(
        review_issues=lambda indices: [
            {"segment_index": indices[-1], "message": "称呼漂移：跨章。"},
        ],
        verdicts={"称呼漂移：跨章。": "confirmed"},
    )

    result = review_preview.run_preview_review(book, client)

    candidates = result["evidence"]["candidates"]
    assert sorted(candidate["candidate"]["chapter"] for candidate in candidates) == [1, 2]
    assert [candidate["candidate"]["segment_index"] for candidate in candidates] == [1, 1]
    assert len(result["reviews"]) == 2
    assert result["metrics"]["confirmed"] == 2
    assert result["decision"]["decision"] == "GO"


def test_run_preview_review_records_high_false_positive_rate_as_no_go(tmp_path: Path) -> None:
    chapter = _chapter(
        1,
        [
            _segment(0, "Chapter One", "第一章", kind="heading"),
            _segment(1, "Draco waited.", "德拉科等待着。"),
            _segment(2, "Harry looked up.", "哈利抬起头。"),
        ],
    )
    book = _write_book(tmp_path, [chapter])
    client = PreviewClient(
        review_issues=lambda indices: [
            {"segment_index": indices[0], "message": "误报一。"},
            {"segment_index": indices[1], "message": "误报二。"},
        ],
        verdicts={"误报一。": "rejected", "误报二。": "rejected"},
    )

    result = review_preview.run_preview_review(book, client)

    assert result["metrics"]["false_positive_rate"] == 1.0
    assert result["decision"]["decision"] == "NO-GO"
    assert any("false-positive rate" in reason for reason in result["decision"]["reasons"])


def test_run_preview_review_flags_unresolved_candidates_for_human_review(tmp_path: Path) -> None:
    chapter = _chapter(
        1,
        [
            _segment(0, "Chapter One", "第一章", kind="heading"),
            _segment(1, "Draco waited.", "德拉科等待着。"),
        ],
    )
    book = _write_book(tmp_path, [chapter])
    client = PreviewClient(
        review_issues=lambda indices: [
            {"segment_index": indices[-1], "message": "待定。"},
        ],
        verdicts={"待定。": "unresolved"},
    )

    result = review_preview.run_preview_review(book, client)

    assert result["metrics"]["unresolved"] == 1
    assert result["metrics"]["false_positive_rate"] == 0.0
    assert result["decision"]["human_review_required"] is True
    assert result["decision"]["decision"] == "GO"


def test_run_preview_review_rejects_a_book_without_chapters(tmp_path: Path) -> None:
    book = tmp_path / "empty"
    book.mkdir()
    with pytest.raises(ValueError, match="chapter"):
        review_preview.run_preview_review(book, PreviewClient())


def test_run_preview_review_uses_the_migration_chunk_reuse_map(tmp_path: Path) -> None:
    source = "Draco waited."
    chapter = _chapter(
        1,
        [
            _segment(
                0,
                source,
                "错误的复用译文。",
                target_origin="polished",
            )
        ],
    )
    book = _write_book(tmp_path, [chapter])
    report = {
        "book_dir": str(book),
        "chunk_ids": ["chunk0001"],
    }
    report_path = book / "work_temp" / preview_migration.MIGRATION_REPORT
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report), encoding="utf-8")

    reuse_map = {segment_state.source_hash(source): {"target": "正确译文。", "origin": "polished"}}
    result = review_preview.run_preview_review(book, PreviewClient(), reuse_map=reuse_map)
    assert result["alignment"]["clean"] is False
    assert result["decision"]["decision"] == "NO-GO"


def test_run_preview_review_is_advisory_and_leaves_formal_text_alone(tmp_path: Path) -> None:
    chapter = _chapter(
        1,
        [
            _segment(0, "Chapter One", "第一章", kind="heading"),
            _segment(1, "Draco waited.", "德拉科等待着。"),
        ],
    )
    book = _write_book(tmp_path, [chapter])
    chapter_path = segment_state.chapter_path(book, 1)
    before = chapter_path.read_text(encoding="utf-8")

    result = review_preview.run_preview_review(book, PreviewClient())

    assert result["advisory"] is True
    assert chapter_path.read_text(encoding="utf-8") == before
    assert not (book / "translation" / "proofread").exists()


def test_load_reuse_map_reads_the_migration_report_and_legacy_chunks(tmp_path: Path) -> None:
    work = tmp_path / "legacy"
    source = "# Chapter One\n\nDraco waited."
    polished = "# 第一章\n\n德拉科等待着。"
    for path, text in (
        (work / "work_temp" / "chunk0001.md", source),
        (work / "translation" / "polished" / "chunk0001.md", polished),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    book = work / "preview_v2"
    report_path = book / "work_temp" / preview_migration.MIGRATION_REPORT
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps({"chunk_ids": ["chunk0001"]}), encoding="utf-8")

    reuse = review_preview.load_reuse_map(book)

    assert reuse is not None
    entry = reuse[segment_state.source_hash("Draco waited.")]
    assert entry["target"] == "德拉科等待着。"
    assert entry["origin"] == "polished"
