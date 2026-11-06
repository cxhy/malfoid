"""Review report writing, verdict aggregation and legacy compatibility tests (ticket 10)."""

from __future__ import annotations

import json
from pathlib import Path

import review_reports


def _segment(index: int, chapter: int = 1) -> dict:
    return {
        "index": index,
        "chapter": chapter,
        "kind": "text",
        "source": "Draco waited.",
        "source_hash": f"hash-{chapter}-{index}",
        "target": "德拉科等待着。",
    }


def _review(
    *,
    indices: list[int] | None = None,
    issues: list[dict] | None = None,
    complete: bool = True,
    error: str | None = None,
) -> dict:
    indices = indices if indices is not None else [0, 1]
    return {
        "segments": indices,
        "start": indices[0],
        "end": indices[-1],
        "issues": issues or [],
        "reviewed_segments": len(indices) if complete else 0,
        "complete": complete,
        "attempts": 1,
        "stage": "block" if complete else "failed",
        "error": error,
    }


def _issue(index: int, message: str = "称呼漂移。", proposal: str | None = None) -> dict:
    issue = {"segment_index": index, "message": message}
    if proposal is not None:
        issue["proposal"] = proposal
    return issue


def _verified(
    *,
    index: int,
    block: int = 0,
    status: str = "confirmed",
    candidate_id: str | None = None,
    message: str = "称呼漂移。",
    proposal: str = "统一称呼。",
) -> dict:
    candidate = {
        "chapter": 1,
        "segment_index": index,
        "message": message,
        "proposal": proposal,
        "block": block,
    }
    candidate["id"] = candidate_id or f"cand-{block}-{index}"
    return {
        "candidate": candidate,
        "id": candidate["id"],
        "status": status,
        "verified": status == "confirmed",
        "needs_human": status == "unresolved",
        "rounds": 1,
        "reason": "证据核对。",
        "requested_terms": [],
        "evidence": [
            {
                "chapter": 1,
                "segment_index": index,
                "ref": f"ch-01:{index}",
                "kind": "text",
                "source": "Draco waited.",
                "target": "德拉科等待着。",
                "reason": "candidate anchor",
            }
        ],
        "error": None,
    }


def _arbitration(
    *,
    conflict_id: str = "conflict-abc",
    candidates: list[str],
    resolved: bool = False,
    winner: str | None = None,
) -> dict:
    return {
        "conflict": conflict_id,
        "key": "ch01:s0",
        "resolved": resolved,
        "winner": winner,
        "reason": "证据不足，留人工裁定。" if not resolved else "以证据为准。",
        "needs_human": not resolved,
        "error": None,
        "candidates": candidates,
    }


def _evidence_result(
    candidates: list[dict],
    arbitrations: list[dict] | None = None,
) -> dict:
    return {
        "candidates": candidates,
        "conflicts": arbitrations or [],
        "unresolved_conflicts": [result for result in arbitrations or [] if result["needs_human"]],
        "confirmed": [record for record in candidates if record["status"] == "confirmed"],
        "rejected": [record for record in candidates if record["status"] == "rejected"],
        "unresolved_candidates": [
            record for record in candidates if record["status"] == "unresolved"
        ],
    }


def test_write_block_report_writes_json_and_markdown(tmp_path: Path) -> None:
    book = tmp_path / "book"
    review = _review(
        issues=[_issue(1, "称呼漂移。", "统一称“德拉科”。")],
        complete=True,
    )
    report = review_reports.build_block_report(review, block_index=0)
    json_path, markdown_path = review_reports.write_block_report(book, report)

    assert json_path == review_reports.block_json_path(book, 0)
    assert markdown_path == review_reports.block_markdown_path(book, 0)
    assert json_path.is_file() and markdown_path.is_file()

    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert data["kind"] == "review-block"
    assert data["block"] == 0
    assert data["verdict"] == "FAIL"
    assert data["issues"][0]["segment_index"] == 1
    assert data["advisory"] is True

    markdown = markdown_path.read_text(encoding="utf-8")
    assert "审查报告" in markdown
    assert "结论：FAIL" in markdown
    assert "统一称“德拉科”" in markdown


def test_block_verdict_passes_a_complete_review_without_issues() -> None:
    assert review_reports.block_verdict(_review(issues=[])) == "PASS"


def test_block_verdict_fails_on_unverified_issues() -> None:
    review = _review(issues=[_issue(0)])
    assert review_reports.block_verdict(review) == "FAIL"


def test_block_verdict_fails_an_incomplete_review() -> None:
    review = _review(issues=[], complete=False, error="model output is not marked complete")
    assert review_reports.block_verdict(review) == "FAIL"


def test_block_verdict_ignores_rejected_candidates() -> None:
    review = _review(issues=[_issue(0)])
    evidence = [_verified(index=0, status="rejected")]
    assert review_reports.block_verdict(review, evidence=evidence) == "PASS"


def test_block_verdict_fails_on_confirmed_or_unresolved_candidates() -> None:
    review = _review(issues=[_issue(0), _issue(1)])
    evidence = [
        _verified(index=0, status="confirmed"),
        _verified(index=1, status="unresolved"),
    ]
    assert review_reports.block_verdict(review, evidence=evidence) == "FAIL"


def test_block_verdict_fails_on_unresolved_arbitration() -> None:
    review = _review(issues=[_issue(0)])
    evidence = [_verified(index=0, status="rejected")]
    arbitration = _arbitration(candidates=["cand-0-0"])
    assert (
        review_reports.block_verdict(review, evidence=evidence, arbitrations=[arbitration])
        == "FAIL"
    )


def test_block_verdict_fails_when_an_issue_has_no_evidence_record() -> None:
    review = _review(issues=[_issue(0), _issue(1)])
    evidence = [_verified(index=0, status="rejected")]
    assert review_reports.block_verdict(review, evidence=evidence) == "FAIL"


def test_write_review_reports_writes_every_block_and_a_summary(tmp_path: Path) -> None:
    book = tmp_path / "book"
    reviews = [
        _review(indices=[0, 1], issues=[]),
        _review(indices=[2, 3], issues=[_issue(2)]),
    ]
    summary = review_reports.write_review_reports(book, reviews)

    assert review_reports.block_json_path(book, 0).is_file()
    assert review_reports.block_markdown_path(book, 0).is_file()
    assert review_reports.block_json_path(book, 1).is_file()
    assert review_reports.block_markdown_path(book, 1).is_file()
    assert review_reports.summary_json_path(book).is_file()
    assert review_reports.summary_markdown_path(book).is_file()

    assert summary["counts"] == {"PASS": 1, "FAIL": 1}
    assert summary["verdict"] == "FAIL"
    assert [block["verdict"] for block in summary["blocks"]] == ["PASS", "FAIL"]

    summary_text = review_reports.summary_markdown_path(book).read_text(encoding="utf-8")
    assert "审查汇总" in summary_text
    assert "PASS：1" in summary_text
    assert "FAIL：1" in summary_text
    assert "block-001" in summary_text
    assert "block-002" in summary_text


def test_reports_are_deterministic(tmp_path: Path) -> None:
    book = tmp_path / "book"
    reviews = [
        _review(indices=[0], issues=[_issue(0, "问题乙。")]),
        _review(indices=[1], issues=[_issue(1, "问题甲。")]),
    ]
    review_reports.write_review_reports(book, reviews)
    first_json = review_reports.summary_json_path(book).read_text(encoding="utf-8")
    first_markdown = review_reports.summary_markdown_path(book).read_text(encoding="utf-8")
    first_block = review_reports.block_markdown_path(book, 0).read_text(encoding="utf-8")

    review_reports.write_review_reports(book, reviews)
    assert review_reports.summary_json_path(book).read_text(encoding="utf-8") == first_json
    assert review_reports.summary_markdown_path(book).read_text(encoding="utf-8") == first_markdown
    assert review_reports.block_markdown_path(book, 0).read_text(encoding="utf-8") == first_block


def test_reports_include_evidence_and_arbitration_results(tmp_path: Path) -> None:
    book = tmp_path / "book"
    review = _review(indices=[0, 1], issues=[_issue(0), _issue(1)])
    confirmed = _verified(index=0, block=0, status="confirmed")
    unresolved = _verified(index=1, block=0, status="unresolved")
    arbitration = _arbitration(
        conflict_id="conflict-draco",
        candidates=[confirmed["id"], unresolved["id"]],
    )
    evidence_result = _evidence_result([confirmed, unresolved], [arbitration])

    review_reports.write_review_reports(book, [review], evidence_result=evidence_result)
    data = json.loads(review_reports.block_json_path(book, 0).read_text(encoding="utf-8"))

    assert data["verdict"] == "FAIL"
    assert data["counts"]["confirmed"] == 1
    assert data["counts"]["unresolved"] == 1
    assert data["counts"]["unresolved_conflicts"] == 1
    assert [record["id"] for record in data["evidence"]] == [confirmed["id"], unresolved["id"]]
    assert data["evidence"][0]["evidence"][0]["ref"] == "ch-01:0"
    assert data["arbitrations"][0]["conflict"] == "conflict-draco"
    assert data["arbitrations"][0]["needs_human"] is True
    assert data["recommendations"]
    assert any(item.get("conflict") == "conflict-draco" for item in data["recommendations"])

    markdown = review_reports.block_markdown_path(book, 0).read_text(encoding="utf-8")
    assert "证据核对" in markdown
    assert "ch-01:0" in markdown
    assert "冲突仲裁" in markdown
    assert "conflict-draco" in markdown


def test_evidence_results_are_filtered_by_block(tmp_path: Path) -> None:
    book = tmp_path / "book"
    reviews = [
        _review(indices=[0], issues=[_issue(0)]),
        _review(indices=[1], issues=[_issue(1)]),
    ]
    evidence_result = _evidence_result(
        [
            _verified(index=0, block=0, status="rejected"),
            _verified(index=1, block=1, status="confirmed"),
        ]
    )
    summary = review_reports.write_review_reports(book, reviews, evidence_result=evidence_result)

    assert summary["blocks"][0]["verdict"] == "PASS"
    assert summary["blocks"][1]["verdict"] == "FAIL"
    block_one = json.loads(review_reports.block_json_path(book, 0).read_text(encoding="utf-8"))
    block_two = json.loads(review_reports.block_json_path(book, 1).read_text(encoding="utf-8"))
    assert [record["id"] for record in block_one["evidence"]] == ["cand-0-0"]
    assert [record["id"] for record in block_two["evidence"]] == ["cand-1-1"]


def test_review_reports_stay_advisory_and_do_not_touch_formal_text(tmp_path: Path) -> None:
    book = tmp_path / "book"
    chapter = book / "state" / "chapter-01.json"
    chapter.parent.mkdir(parents=True)
    chapter.write_text(
        json.dumps({"chapter": 1, "segments": [_segment(0)]}, ensure_ascii=False),
        encoding="utf-8",
    )
    polished = book / "translation" / "polished" / "chapter-01.md"
    polished.parent.mkdir(parents=True)
    polished.write_text("德拉科等待着。\n", encoding="utf-8")
    before_chapter = chapter.read_bytes()
    before_polished = polished.read_bytes()

    review_reports.write_review_reports(
        book,
        [_review(indices=[0], issues=[_issue(0, "称呼漂移。", "改为“德拉科”。")])],
    )

    assert chapter.read_bytes() == before_chapter
    assert polished.read_bytes() == before_polished
    report = json.loads(review_reports.block_json_path(book, 0).read_text(encoding="utf-8"))
    assert report["recommendations"][0]["proposal"] == "改为“德拉科”。"
    assert report["advisory"] is True


def test_read_report_reads_a_v2_json_report(tmp_path: Path) -> None:
    book = tmp_path / "book"
    review_reports.write_review_reports(book, [_review(indices=[0], issues=[])])
    report = review_reports.read_report(review_reports.block_json_path(book, 0))
    assert report["kind"] == "review-block"
    assert report["verdict"] == "PASS"


def test_read_report_reads_a_legacy_proofread_report(tmp_path: Path) -> None:
    legacy = tmp_path / "proofread" / "chunk0001.md"
    legacy.parent.mkdir(parents=True)
    legacy.write_text(
        "## 严重\n\n- 【误译】译文：“甲”｜原文：“A”｜建议：改为“乙”\n\n"
        "## 一般\n\n- 【术语】译文：“丙”｜原文：“C”｜建议：改为“丁”\n\n"
        "结论：FAIL\n",
        encoding="utf-8",
    )

    report = review_reports.read_report(legacy)
    assert report["kind"] == "legacy-proofread"
    assert report["verdict"] == "FAIL"
    assert report["issues"] == 2
    assert report["complete"] is True
    assert "结论：FAIL" in report["raw"]

    passing = tmp_path / "proofread" / "chunk0002.md"
    passing.write_text("结论：PASS\n", encoding="utf-8")
    assert review_reports.read_report(passing)["verdict"] == "PASS"


def test_read_legacy_summary_remains_readable(tmp_path: Path) -> None:
    summary = tmp_path / "proofread" / "summary.md"
    summary.parent.mkdir(parents=True)
    summary.write_text(
        "# 校对汇总 1–2\n\n"
        "| chunk | 结论 | 问题数 |\n"
        "|---|---|---|\n"
        "| chunk0001 | FAIL | 2 |\n"
        "| chunk0002 | PASS | 0 |\n",
        encoding="utf-8",
    )
    report = review_reports.read_legacy_summary(summary)
    assert report["kind"] == "legacy-proofread-summary"
    assert report["counts"] == {"FAIL": 1, "PASS": 1}
    assert [block["chunk"] for block in report["blocks"]] == ["chunk0001", "chunk0002"]
    assert [block["issues"] for block in report["blocks"]] == [2, 0]


def test_legacy_proofread_reports_survive_review_report_writes(tmp_path: Path) -> None:
    book = tmp_path / "book"
    legacy = book / "translation" / "proofread" / "chunk0001.md"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("结论：PASS\n", encoding="utf-8")
    before = legacy.read_bytes()

    review_reports.write_review_reports(book, [_review(indices=[0], issues=[])])

    assert legacy.read_bytes() == before
    assert review_reports.read_report(legacy)["verdict"] == "PASS"
    assert review_reports.review_dir(book) == book / "translation" / "review"
