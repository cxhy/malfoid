"""Review-issue fix pass tests."""

from __future__ import annotations

import json
from pathlib import Path

import fix_review_issues
import pytest
import segment_state


def _segment(index: int, source: str, target: str) -> dict:
    return {
        "index": index,
        "kind": "text",
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


def _review_report(issues: list[dict], evidence: list[dict]) -> dict:
    return {"issues": issues, "evidence": evidence}


def _evidence(issue: dict, status: str) -> dict:
    candidate = {
        "chapter": issue["chapter"],
        "segment_index": issue["segment_index"],
        "message": issue["message"],
    }
    return {"candidate": candidate, "status": status}


def test_collect_targets_keeps_only_confirmed_actionable_issues(tmp_path: Path) -> None:
    review_dir = tmp_path / "review"
    review_dir.mkdir()
    issues = [
        {
            "chapter": 1,
            "segment_index": 0,
            "message": "称呼漂移。",
            "proposal": "统一为马尔福。",
        },
        {
            "chapter": 1,
            "segment_index": 1,
            "message": "译文没有问题。",
            "proposal": "无需修改。",
        },
        {
            "chapter": 1,
            "segment_index": 2,
            "message": "原文疑似笔误。",
            "proposal": "在译文中标注原文笔误。",
        },
    ]
    evidence = [
        _evidence(issues[0], "confirmed"),
        _evidence(issues[1], "confirmed"),
        _evidence(issues[2], "confirmed"),
    ]
    (review_dir / "block-001.json").write_text(
        json.dumps(_review_report(issues, evidence), ensure_ascii=False), encoding="utf-8"
    )
    targets, skipped = fix_review_issues.collect_targets(review_dir)
    assert [(target.chapter, target.index) for target in targets] == [(1, 0)]
    assert len(skipped) == 2


def test_collect_targets_deduplicates_issues_for_one_segment(tmp_path: Path) -> None:
    review_dir = tmp_path / "review"
    review_dir.mkdir()
    first = {
        "chapter": 1,
        "segment_index": 0,
        "message": "问题一。",
        "proposal": "改法一。",
    }
    second = {
        "chapter": 1,
        "segment_index": 0,
        "message": "问题二。",
        "proposal": "改法二。",
    }
    evidence = [_evidence(first, "confirmed"), _evidence(second, "confirmed")]
    (review_dir / "block-001.json").write_text(
        json.dumps(_review_report([first, second], evidence), ensure_ascii=False),
        encoding="utf-8",
    )
    targets, skipped = fix_review_issues.collect_targets(review_dir)
    assert len(targets) == 1
    assert len(targets[0].issues) == 2
    assert skipped == []


def test_parse_fix_output_accepts_json_and_fences() -> None:
    assert fix_review_issues.parse_fix_output('{"target": "修正后的译文"}') == "修正后的译文"
    fenced = '```json\n{"target": "另一句"}\n```'
    assert fix_review_issues.parse_fix_output(fenced) == "另一句"


def test_parse_fix_output_rejects_empty_target() -> None:
    with pytest.raises(ValueError, match="target"):
        fix_review_issues.parse_fix_output('{"target": ""}')


def test_validate_fix_rejects_forbidden_names_and_english() -> None:
    target = fix_review_issues.FixTarget(1, 0, "Malfoy waited.", "马尔福等待着。", [])
    for bad in ("德拉科等待着。", "达西等待着。", "Malfoy waited."):
        with pytest.raises(ValueError):
            fix_review_issues.validate_fix(target, bad)
    fix_review_issues.validate_fix(target, "马尔福在那儿等着。")


def test_apply_fixes_writes_targets_and_records_failures(tmp_path: Path) -> None:
    book = tmp_path / "book"
    chapter = _chapter(
        1,
        [
            _segment(0, "Malfoy waited.", "德拉科妮娅·马尔福等待着。"),
            _segment(1, "Harry looked up.", "哈利抬起头。"),
        ],
    )
    segment_state.save_chapter(segment_state.chapter_path(book, 1), chapter)
    targets = [
        fix_review_issues.FixTarget(
            1,
            0,
            "Malfoy waited.",
            "德拉科妮娅·马尔福等待着。",
            [{"message": "原文只用姓氏。", "proposal": "改为马尔福。"}],
        ),
        fix_review_issues.FixTarget(
            1,
            1,
            "Harry looked up.",
            "哈利抬起头。",
            [{"message": "问题。", "proposal": "改法。"}],
        ),
    ]

    class Client:
        def __init__(self) -> None:
            self.calls = 0

        def __call__(self, prompt: str) -> str:
            self.calls += 1
            if "哈利抬起头" in prompt:
                return '{"target": ""}'
            return '{"target": "马尔福等待着。"}'

    result = fix_review_issues.apply_fixes(book, targets, Client(), retries=0)
    assert result["applied"] == 1
    assert result["failed"] == 1
    saved = segment_state.load_chapter(segment_state.chapter_path(book, 1))
    assert saved["segments"][0]["target"] == "马尔福等待着。"
    assert saved["segments"][1]["target"] == "哈利抬起头。"


def test_apply_fixes_dry_run_leaves_state_unchanged(tmp_path: Path) -> None:
    book = tmp_path / "book"
    chapter = _chapter(1, [_segment(0, "Malfoy waited.", "德拉科妮娅·马尔福等待着。")])
    segment_state.save_chapter(segment_state.chapter_path(book, 1), chapter)
    target = fix_review_issues.FixTarget(
        1, 0, "Malfoy waited.", "德拉科妮娅·马尔福等待着。", [{"message": "x", "proposal": "y"}]
    )
    result = fix_review_issues.apply_fixes(
        book, [target], lambda prompt: '{"target": "马尔福等待着。"}', dry_run=True
    )
    assert result["applied"] == 1
    saved = segment_state.load_chapter(segment_state.chapter_path(book, 1))
    assert saved["segments"][0]["target"] == "德拉科妮娅·马尔福等待着。"


def test_apply_fixes_is_idempotent_and_skips_applied_targets(tmp_path: Path) -> None:
    book = tmp_path / "book"
    chapter = _chapter(1, [_segment(0, "Malfoy waited.", "德拉科妮娅·马尔福等待着。")])
    segment_state.save_chapter(segment_state.chapter_path(book, 1), chapter)
    target = fix_review_issues.FixTarget(
        1,
        0,
        "Malfoy waited.",
        "德拉科妮娅·马尔福等待着。",
        [{"message": "原文只用姓氏。", "proposal": "改为马尔福。"}],
    )

    class Client:
        def __init__(self) -> None:
            self.calls = 0

        def __call__(self, prompt: str) -> str:
            self.calls += 1
            return '{"target": "马尔福等待着。"}'

    client = Client()
    first = fix_review_issues.apply_fixes(book, [target], client, retries=0)
    assert first["applied"] == 1
    assert client.calls == 1

    second = fix_review_issues.apply_fixes(book, [target], client, retries=0)
    assert second["applied"] == 0
    assert second["skipped_applied"] == 1
    assert client.calls == 1
