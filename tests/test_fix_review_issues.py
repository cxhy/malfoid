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
            "category": "consistency",
            "severity": "medium",
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
        "category": "mistranslation",
        "severity": "high",
    }
    second = {
        "chapter": 1,
        "segment_index": 0,
        "message": "问题二。",
        "proposal": "改法二。",
        "category": "omission",
        "severity": "medium",
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


def test_apply_fixes_dry_run_is_plan_only_and_makes_no_model_calls(tmp_path: Path) -> None:
    book = tmp_path / "book"
    chapter = _chapter(1, [_segment(0, "Malfoy waited.", "德拉科妮娅·马尔福等待着。")])
    segment_state.save_chapter(segment_state.chapter_path(book, 1), chapter)
    target = fix_review_issues.FixTarget(
        1, 0, "Malfoy waited.", "德拉科妮娅·马尔福等待着。", [{"message": "x", "proposal": "y"}]
    )
    calls: list[str] = []

    def client(prompt: str) -> str:
        calls.append(prompt)
        return '{"target": "马尔福等待着。"}'

    result = fix_review_issues.apply_fixes(book, [target], client, dry_run=True)
    assert result["planned"] == 1
    assert result["applied"] == 0
    assert calls == []
    assert result["records"][0]["status"] == "planned"
    assert result["manual_queue_md"] is None
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


def test_validate_fix_accepts_a_source_based_correction_for_a_partial_target() -> None:
    source = (
        "A sudden silence fell downstairs. Two seconds later Harry, heart thudding "
        'madly, heard Uncle Vernon coming into the hall, calling, "Dudley must have '
        'left his television on again, the little tyke!"'
    )
    target = fix_review_issues.FixTarget(2, 67, source, "“达力肯定又开着电视忘了关，这小淘气！”", [])
    fixed = (
        "楼下突然一片寂静。两秒后，哈利心脏狂跳，听见弗农姨父走进门厅，喊道："
        "“达力肯定又开着电视忘了关，这小淘气！”"
    )
    fix_review_issues.validate_fix(target, fixed)


def test_validate_fix_rejects_an_implausibly_short_fix() -> None:
    source = "Harry looked up at the ceiling and waited for the answer in silence."
    target = fix_review_issues.FixTarget(1, 0, source, "哈利抬起头。", [])
    with pytest.raises(ValueError, match="ratio"):
        fix_review_issues.validate_fix(target, "等。")


def test_apply_fixes_writes_a_manual_queue_for_failed_targets(tmp_path: Path) -> None:
    book = tmp_path / "book"
    chapter = _chapter(1, [_segment(0, "Harry looked up.", "哈利抬起头。")])
    segment_state.save_chapter(segment_state.chapter_path(book, 1), chapter)
    target = fix_review_issues.FixTarget(
        1, 0, "Harry looked up.", "哈利抬起头。", [{"message": "漏译。", "proposal": "补全。"}]
    )
    result = fix_review_issues.apply_fixes(
        book, [target], lambda prompt: '{"target": ""}', retries=0
    )
    assert result["failed"] == 1
    queue_json = book / "work_temp" / "fix_review" / "manual_queue.json"
    queue_md = book / "work_temp" / "fix_review" / "manual_queue.md"
    assert queue_json.is_file()
    assert queue_md.is_file()
    records = json.loads(queue_json.read_text(encoding="utf-8"))
    assert records[0]["source"] == "Harry looked up."
    assert records[0]["current"] == "哈利抬起头。"


def test_apply_fixes_injects_relevant_glossary_terms(tmp_path: Path) -> None:
    book = tmp_path / "book"
    chapter = _chapter(1, [_segment(0, "Kwikspell letter.", "速成咒术课程的信。")])
    segment_state.save_chapter(segment_state.chapter_path(book, 1), chapter)
    target = fix_review_issues.FixTarget(
        1, 0, "Kwikspell letter.", "速成咒术课程的信。", [{"message": "x", "proposal": "y"}]
    )
    prompts: list[str] = []

    def client(prompt: str) -> str:
        prompts.append(prompt)
        return '{"target": "速成咒术课程的信。"}'

    fix_review_issues.apply_fixes(
        book,
        [target],
        client,
        glossary_terms=[{"source": "Kwikspell", "target": "速成咒术课程"}],
    )
    assert prompts and "速成咒术课程" in prompts[0]


def test_collect_targets_skips_style_and_uncategorized_issues(tmp_path: Path) -> None:
    review_dir = tmp_path / "review"
    review_dir.mkdir()
    issues = [
        {
            "chapter": 1,
            "segment_index": 0,
            "message": "句子节奏偏慢。",
            "proposal": "调整节奏。",
            "category": "style",
            "severity": "low",
        },
        {
            "chapter": 1,
            "segment_index": 1,
            "message": "需要人工确认。",
            "proposal": "人工核对。",
        },
        {
            "chapter": 1,
            "segment_index": 2,
            "message": "漏译一句。",
            "proposal": "补全。",
            "category": "omission",
            "severity": "high",
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
    assert [(target.chapter, target.index) for target in targets] == [(1, 2)]
    reasons = {item["reason"] for item in skipped}
    assert "style suggestion" in reasons
    assert "non-actionable category: other" in reasons


def test_classify_issue_infers_category_and_severity() -> None:
    assert fix_review_issues.classify_issue(
        {"message": "原文漏译了一句。", "proposal": "补全。"}
    ) == ("omission", "high")
    assert fix_review_issues.classify_issue(
        {"message": "称呼漂移。", "proposal": "统一。"}
    ) == ("consistency", "medium")
    assert fix_review_issues.classify_issue(
        {"message": "句子节奏偏慢。", "proposal": "调整。"}
    ) == ("style", "low")
    assert fix_review_issues.classify_issue(
        {"message": "需要人工确认。", "proposal": "核对。"}
    ) == ("other", "medium")


def test_classify_issue_marks_advisory_checks_low() -> None:
    assert fix_review_issues.classify_issue(
        {"message": "术语正确；需确认全书统一。", "proposal": "核对。"}
    ) == ("terminology", "low")


def test_collect_targets_skips_low_severity_issues(tmp_path: Path) -> None:
    review_dir = tmp_path / "review"
    review_dir.mkdir()
    issue = {
        "chapter": 1,
        "segment_index": 0,
        "message": "称呼需确认全书统一。",
        "proposal": "核对。",
        "category": "consistency",
        "severity": "low",
    }
    evidence = [_evidence(issue, "confirmed")]
    (review_dir / "block-001.json").write_text(
        json.dumps(_review_report([issue], evidence), ensure_ascii=False), encoding="utf-8"
    )
    targets, skipped = fix_review_issues.collect_targets(review_dir)
    assert targets == []
    assert skipped[0]["reason"] == "low severity: low"


def test_collect_targets_records_the_review_time_target(tmp_path: Path) -> None:
    review_dir = tmp_path / "review"
    review_dir.mkdir()
    issue = {
        "chapter": 1,
        "segment_index": 0,
        "message": "漏译。",
        "proposal": "补全。",
        "category": "omission",
        "severity": "high",
    }
    record = _evidence(issue, "confirmed")
    record["evidence"] = [
        {"chapter": 1, "segment_index": 0, "target": "审查时的旧译文。"}
    ]
    (review_dir / "block-001.json").write_text(
        json.dumps(_review_report([issue], [record]), ensure_ascii=False), encoding="utf-8"
    )
    targets, _ = fix_review_issues.collect_targets(review_dir)
    assert targets[0].review_target == "审查时的旧译文。"


def test_apply_fixes_skips_targets_changed_since_review(tmp_path: Path) -> None:
    book = tmp_path / "book"
    chapter = _chapter(1, [_segment(0, "Harry looked up.", "哈利抬起头。")])
    segment_state.save_chapter(segment_state.chapter_path(book, 1), chapter)
    target = fix_review_issues.FixTarget(
        1,
        0,
        "Harry looked up.",
        "哈利抬起头。",
        [{"message": "漏译。", "proposal": "补全。"}],
        review_target="审查时的旧译文。",
    )
    calls: list[str] = []

    def client(prompt: str) -> str:
        calls.append(prompt)
        return '{"target": "哈利抬起头，看了看。"}'

    result = fix_review_issues.apply_fixes(book, [target], client)
    assert result["skipped_changed"] == 1
    assert result["applied"] == 0
    assert calls == []
    saved = segment_state.load_chapter(segment_state.chapter_path(book, 1))
    assert saved["segments"][0]["target"] == "哈利抬起头。"


def test_collect_targets_allows_low_severity_glossary_variant(tmp_path: Path) -> None:
    review_dir = tmp_path / "review"
    review_dir.mkdir()
    issue = {
        "chapter": 1,
        "segment_index": 0,
        "message": "咒语译名错误：利维科应为倒挂金钟。",
        "proposal": "改为“倒挂金…”。",
        "category": "terminology",
        "severity": "low",
    }
    record = _evidence(issue, "confirmed")
    record["evidence"] = [
        {
            "chapter": 1,
            "segment_index": 0,
            "target": "“你敢！利维科……”她没能念完咒语。",
        }
    ]
    (review_dir / "block-001.json").write_text(
        json.dumps(_review_report([issue], [record]), ensure_ascii=False),
        encoding="utf-8",
    )
    glossary = [
        {
            "id": "Levicorpus",
            "source": "Levicorpus",
            "target": "倒挂金钟",
            "forbidden_targets": ["利维科", "利维科斯"],
        }
    ]
    targets, skipped = fix_review_issues.collect_targets(
        review_dir, glossary_terms=glossary
    )
    assert len(targets) == 1
    assert targets[0].issues[0]["glossary_variant"] == "利维科"
    assert skipped == []


def test_collect_targets_still_skips_low_severity_without_glossary_variant(
    tmp_path: Path,
) -> None:
    review_dir = tmp_path / "review"
    review_dir.mkdir()
    issue = {
        "chapter": 1,
        "segment_index": 0,
        "message": "称呼需确认全书统一。",
        "proposal": "核对。",
        "category": "consistency",
        "severity": "low",
    }
    record = _evidence(issue, "confirmed")
    record["evidence"] = [
        {"chapter": 1, "segment_index": 0, "target": "马尔福等待着。"}
    ]
    (review_dir / "block-001.json").write_text(
        json.dumps(_review_report([issue], [record]), ensure_ascii=False),
        encoding="utf-8",
    )
    targets, skipped = fix_review_issues.collect_targets(
        review_dir,
        glossary_terms=[
            {
                "id": "Levicorpus",
                "source": "Levicorpus",
                "target": "倒挂金钟",
                "forbidden_targets": ["利维科"],
            }
        ],
    )
    assert targets == []
    assert skipped[0]["reason"] == "low severity: low"
