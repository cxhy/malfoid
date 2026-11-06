"""Structured review reports and PASS/FAIL summaries (pipeline v2, ticket 10).

Each review block writes a structured JSON report and a human-readable Markdown
summary under ``translation/review``. Block verdicts are deterministic: a block
passes only when it is complete, every reported issue has a verification record,
no candidate is confirmed or unresolved, and no conflict needs a human decision.

The report layer is advisory. It reads structured review block results and
evidence/arbitration results through their public interfaces, writes reports and
recommendations, and never edits formal text. Legacy proofread reports under
``translation/proofread`` remain readable through :func:`read_report`,
:func:`read_legacy_proofread` and :func:`read_legacy_summary`.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

REVIEW_DIR = "review"
REPORT_VERSION = 1
PASS = "PASS"
FAIL = "FAIL"

_LEGACY_VERDICT_RE = re.compile(r"结论[:：]\s*(PASS|FAIL)")


def _legacy_verdict(path: Path) -> str:
    if not path.is_file():
        return "MISSING"
    match = _LEGACY_VERDICT_RE.search(path.read_text(encoding="utf-8"))
    return match.group(1) if match else "UNKNOWN"


def _legacy_issue_count(path: Path) -> int:
    if not path.is_file():
        return 0
    return len(re.findall(r"(?m)^- 【[^】]*】", path.read_text(encoding="utf-8")))


_LEGACY_ROW_RE = re.compile(
    r"^\|\s*([^|]+?)\s*\|\s*(PASS|FAIL|UNKNOWN|MISSING)\s*\|\s*(\d+)\s*\|",
    re.MULTILINE,
)


def review_dir(book_dir: Path) -> Path:
    """Return the review artifact directory for a book sub-work."""
    return book_dir / "translation" / REVIEW_DIR


def _block_label(block_index: int) -> str:
    return f"block-{block_index + 1:03d}"


def block_json_path(book_dir: Path, block_index: int) -> Path:
    return review_dir(book_dir) / f"{_block_label(block_index)}.json"


def block_markdown_path(book_dir: Path, block_index: int) -> Path:
    return review_dir(book_dir) / f"{_block_label(block_index)}.md"


def summary_json_path(book_dir: Path) -> Path:
    return review_dir(book_dir) / "summary.json"


def summary_markdown_path(book_dir: Path) -> Path:
    return review_dir(book_dir) / "summary.md"


def _issues(review: dict[str, Any]) -> list[dict[str, Any]]:
    issues = review.get("issues")
    if issues is None:
        return []
    if not isinstance(issues, list):
        raise ValueError("review issues must be a JSON array")
    return [issue for issue in issues if isinstance(issue, dict)]


def _evidence_records(evidence: Any) -> list[dict[str, Any]] | None:
    if evidence is None:
        return None
    records = evidence.get("candidates") if isinstance(evidence, dict) else evidence
    if not isinstance(records, list):
        raise ValueError("evidence results must be a list of verification records")
    return [record for record in records if isinstance(record, dict)]


def _arbitration_records(arbitrations: Any) -> list[dict[str, Any]] | None:
    if arbitrations is None:
        return None
    records = arbitrations.get("conflicts") if isinstance(arbitrations, dict) else arbitrations
    if not isinstance(records, list):
        raise ValueError("arbitration results must be a list of conflict records")
    return [record for record in records if isinstance(record, dict)]


def _index_key(item: dict[str, Any]) -> tuple[int, str]:
    try:
        index = int(item.get("segment_index"))
    except (TypeError, ValueError):
        index = -1
    return index, str(item.get("message") or "")


def _record_key(record: dict[str, Any]) -> tuple[int, str]:
    candidate = record.get("candidate") or {}
    try:
        index = int(candidate.get("segment_index"))
    except (TypeError, ValueError):
        index = -1
    return index, str(record.get("id") or "")


def _arbitration_key(record: dict[str, Any]) -> str:
    return str(record.get("conflict") or "")


def _same_issue(issue: dict[str, Any], record: dict[str, Any]) -> bool:
    """Match an issue to its evidence record by segment, message and chapter."""
    candidate = record.get("candidate") or {}
    try:
        issue_index = int(issue.get("segment_index"))
        record_index = int(candidate.get("segment_index"))
    except (TypeError, ValueError):
        return False
    if issue_index != record_index:
        return False
    if str(issue.get("message") or "") != str(candidate.get("message") or ""):
        return False
    issue_chapter = issue.get("chapter")
    record_chapter = candidate.get("chapter")
    if issue_chapter is not None and record_chapter is not None:
        try:
            if int(issue_chapter) != int(record_chapter):
                return False
        except (TypeError, ValueError):
            return False
    return True


def block_verdict(
    review: dict[str, Any],
    evidence: Any = None,
    arbitrations: Any = None,
) -> str:
    """Return the deterministic PASS/FAIL verdict for one review block.

    An incomplete block fails. Without evidence results, any reported issue fails
    the block. With evidence results, rejected candidates are ignored, while
    confirmed, unresolved, or unverified issues fail. Any arbitration that needs a
    human decision fails the block.
    """
    if review.get("complete") is not True:
        return FAIL

    issues = _issues(review)
    records = _evidence_records(evidence)
    arbitration_records = _arbitration_records(arbitrations) or []
    if any(record.get("needs_human") is True for record in arbitration_records):
        return FAIL

    if records is None:
        return FAIL if issues else PASS
    for issue in issues:
        matches = [record for record in records if _same_issue(issue, record)]
        if not matches:
            return FAIL
        if any(record.get("status") != "rejected" for record in matches):
            return FAIL
    return PASS


def _counts(
    issues: list[dict[str, Any]],
    records: list[dict[str, Any]] | None,
    arbitrations: list[dict[str, Any]] | None,
) -> dict[str, int]:
    records = records or []
    arbitrations = arbitrations or []
    return {
        "issues": len(issues),
        "evidence": len(records),
        "confirmed": sum(record.get("status") == "confirmed" for record in records),
        "rejected": sum(record.get("status") == "rejected" for record in records),
        "unresolved": sum(record.get("status") == "unresolved" for record in records),
        "arbitrations": len(arbitrations),
        "unresolved_conflicts": sum(record.get("needs_human") is True for record in arbitrations),
    }


def _recommendations(
    issues: list[dict[str, Any]],
    records: list[dict[str, Any]] | None,
    arbitrations: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    recommendations: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    if records is None:
        for issue in issues:
            recommendations.append(
                {
                    "segment_index": issue.get("segment_index"),
                    "message": issue.get("message") or "",
                    "proposal": issue.get("proposal") or issue.get("message") or "",
                    "status": "unverified",
                    "candidate": None,
                }
            )
    else:
        for record in records:
            if record.get("status") == "rejected":
                continue
            candidate = record.get("candidate") or {}
            key = ("candidate", str(record.get("id") or ""))
            if key in seen:
                continue
            seen.add(key)
            recommendations.append(
                {
                    "segment_index": candidate.get("segment_index"),
                    "message": candidate.get("message") or "",
                    "proposal": candidate.get("proposal") or candidate.get("message") or "",
                    "status": record.get("status"),
                    "candidate": record.get("id"),
                }
            )

    for arbitration in arbitrations or []:
        if arbitration.get("needs_human") is not True:
            continue
        key = ("conflict", str(arbitration.get("conflict") or ""))
        if key in seen:
            continue
        seen.add(key)
        recommendations.append(
            {
                "conflict": arbitration.get("conflict"),
                "status": "unresolved_conflict",
                "reason": arbitration.get("reason") or "",
            }
        )
    return recommendations


def build_block_report(
    review: dict[str, Any],
    *,
    block_index: int = 0,
    evidence: Any = None,
    arbitrations: Any = None,
) -> dict[str, Any]:
    """Build the structured report for one review block.

    ``evidence`` accepts a list of verification records or a full evidence result
    dictionary. When a dictionary is given, records are expected to carry their
    ``candidate.block``; callers that already filtered by block can pass a list.
    """
    if not isinstance(review, dict):
        raise ValueError("review result must be a dict")
    block_index = int(block_index)
    if block_index < 0:
        raise ValueError("block_index must not be negative")

    issues = _issues(review)
    records = _evidence_records(evidence)
    arbitration_records = _arbitration_records(arbitrations)
    sorted_issues = sorted(issues, key=_index_key)
    sorted_records = sorted(records or [], key=_record_key)
    sorted_arbitrations = sorted(arbitration_records or [], key=_arbitration_key)
    return {
        "version": REPORT_VERSION,
        "kind": "review-block",
        "block": block_index,
        "segments": list(review.get("segments") or []),
        "start": review.get("start"),
        "end": review.get("end"),
        "complete": review.get("complete") is True,
        "reviewed_segments": review.get("reviewed_segments", 0),
        "attempts": review.get("attempts", 0),
        "stage": review.get("stage"),
        "error": review.get("error"),
        "verdict": block_verdict(review, evidence=records, arbitrations=sorted_arbitrations),
        "advisory": True,
        "issues": sorted_issues,
        "evidence": sorted_records,
        "arbitrations": sorted_arbitrations,
        "counts": _counts(issues, records, arbitration_records),
        "recommendations": _recommendations(issues, records, arbitration_records),
    }


def _cell(value: Any) -> str:
    text = str(value or "")
    return text.replace("|", "\\|").replace("\n", " ")


def _evidence_status(issue: dict[str, Any], records: list[dict[str, Any]]) -> str:
    statuses: set[str] = set()
    for record in records:
        candidate = record.get("candidate") or {}
        if candidate.get("segment_index") == issue.get("segment_index"):
            statuses.add(str(record.get("status") or "unknown"))
    return "/".join(sorted(statuses)) if statuses else "未核对"


def render_block_markdown(report: dict[str, Any]) -> str:
    """Render one review block report as a human-readable Markdown summary."""
    block = int(report.get("block") or 0)
    label = _block_label(block)
    issues = report.get("issues") or []
    records = report.get("evidence") or []
    arbitrations = report.get("arbitrations") or []
    counts = report.get("counts") or {}

    lines = [
        f"# 审查报告 {label}",
        "",
        f"- 段落范围：{report.get('start')}–{report.get('end')}",
        f"- 结论：{report.get('verdict')}",
        f"- 完整：{'是' if report.get('complete') else '否'}",
        f"- 已审段落：{report.get('reviewed_segments')}",
        f"- 问题数：{counts.get('issues', len(issues))}",
        f"- 证据核对：{counts.get('evidence', len(records))}",
        f"- 冲突仲裁：{counts.get('arbitrations', len(arbitrations))}",
    ]
    if report.get("error"):
        lines.append(f"- 错误：{report['error']}")
    lines.extend(["", "## 问题", ""])
    if not issues:
        lines.append("未报告问题。")
    else:
        lines.append("| 段 | 问题 | 建议 | 证据状态 |")
        lines.append("|---|---|---|---|")
        for issue in issues:
            proposal = issue.get("proposal") or issue.get("message") or ""
            lines.append(
                f"| {_cell(issue.get('segment_index'))} | {_cell(issue.get('message'))} "
                f"| {_cell(proposal)} | {_cell(_evidence_status(issue, records))} |"
            )

    lines.extend(["", "## 证据核对", ""])
    if not records:
        lines.append("无证据核对结果。")
    else:
        for record in records:
            candidate = record.get("candidate") or {}
            position = f"{candidate.get('chapter')}:{candidate.get('segment_index')}"
            lines.append(
                f"- {record.get('id')}：段 {position} 状态 {record.get('status')}"
                f"（{record.get('reason') or ''}）"
            )
            for ref in record.get("evidence") or []:
                lines.append(
                    f"  - [{ref.get('ref')}] chapter={ref.get('chapter')} "
                    f"segment={ref.get('segment_index')} reason={ref.get('reason')}"
                )

    lines.extend(["", "## 冲突仲裁", ""])
    if not arbitrations:
        lines.append("无冲突仲裁结果。")
    else:
        for arbitration in arbitrations:
            state = "已裁决" if arbitration.get("resolved") else "待人工裁定"
            lines.append(
                f"- {arbitration.get('conflict')}：{state}；"
                f"winner={arbitration.get('winner') or '无'}；"
                f"{arbitration.get('reason') or ''}"
            )

    recommendations = report.get("recommendations") or []
    lines.extend(["", "## 建议", ""])
    if not recommendations:
        lines.append("无建议。")
    else:
        for recommendation in recommendations:
            if recommendation.get("conflict"):
                lines.append(
                    f"- 冲突 {recommendation['conflict']}："
                    f"{recommendation.get('reason') or '留人工裁定'}"
                )
            else:
                lines.append(
                    f"- 段 {recommendation.get('segment_index')}："
                    f"{recommendation.get('proposal') or recommendation.get('message') or ''}"
                    f"（{recommendation.get('status') or ''}）"
                )

    lines.extend(["", "> 审查仅提供建议，未修改正文。", ""])
    return "\n".join(lines)


def write_block_report(book_dir: Path, report: dict[str, Any]) -> tuple[Path, Path]:
    """Write one block report as JSON and Markdown; return both paths."""
    if not isinstance(report, dict):
        raise ValueError("report must be a dict")
    block_index = int(report.get("block", 0))
    if block_index < 0:
        raise ValueError("report block index must not be negative")

    json_path = block_json_path(book_dir, block_index)
    markdown_path = block_markdown_path(book_dir, block_index)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    markdown_path.write_text(render_block_markdown(report), encoding="utf-8")
    return json_path, markdown_path


def build_summary(reports: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate block reports into a deterministic PASS/FAIL summary."""
    blocks: list[dict[str, Any]] = []
    counts = {PASS: 0, FAIL: 0}
    for report in reports:
        verdict = str(report.get("verdict") or FAIL)
        if verdict in counts:
            counts[verdict] += 1
        report_counts = report.get("counts") or {}
        blocks.append(
            {
                "block": report.get("block"),
                "label": _block_label(int(report.get("block") or 0)),
                "segments": list(report.get("segments") or []),
                "start": report.get("start"),
                "end": report.get("end"),
                "verdict": verdict,
                "complete": report.get("complete") is True,
                "issues": report_counts.get("issues", len(report.get("issues") or [])),
                "confirmed": report_counts.get("confirmed", 0),
                "rejected": report_counts.get("rejected", 0),
                "unresolved": report_counts.get("unresolved", 0),
                "unresolved_conflicts": report_counts.get("unresolved_conflicts", 0),
                "error": report.get("error"),
            }
        )
    return {
        "version": REPORT_VERSION,
        "kind": "review-summary",
        "advisory": True,
        "verdict": FAIL if counts[FAIL] else PASS,
        "counts": counts,
        "blocks": blocks,
    }


def render_summary_markdown(summary: dict[str, Any]) -> str:
    """Render the review summary as a human-readable Markdown table."""
    counts = summary.get("counts") or {}
    blocks = summary.get("blocks") or []
    lines = [
        "# 审查汇总",
        "",
        f"- 块数：{len(blocks)}",
        f"- PASS：{counts.get(PASS, 0)}",
        f"- FAIL：{counts.get(FAIL, 0)}",
        f"- 总评：{summary.get('verdict')}",
        "",
        "| block | 段落范围 | 结论 | 问题 | 已确认 | 待人工 | 未决冲突 |",
        "|---|---|---|---|---|---|---|",
    ]
    for block in blocks:
        lines.append(
            f"| {block.get('label')} | {block.get('start')}–{block.get('end')} "
            f"| {block.get('verdict')} | {block.get('issues', 0)} "
            f"| {block.get('confirmed', 0)} | {block.get('unresolved', 0)} "
            f"| {block.get('unresolved_conflicts', 0)} |"
        )
    lines.extend(["", "> 审查仅提供建议，未修改正文。", ""])
    return "\n".join(lines)


def _filter_evidence_for_block(
    evidence_result: Any, block_index: int
) -> list[dict[str, Any]] | None:
    records = _evidence_records(evidence_result)
    if records is None:
        return None
    if not isinstance(evidence_result, dict):
        return records
    filtered: list[dict[str, Any]] = []
    for record in records:
        candidate = record.get("candidate") or {}
        try:
            block = int(candidate.get("block"))
        except (TypeError, ValueError):
            continue
        if block == block_index:
            filtered.append(record)
    return filtered


def _filter_arbitrations_for_block(
    evidence_result: Any, block_index: int
) -> list[dict[str, Any]] | None:
    records = _arbitration_records(evidence_result)
    if records is None:
        return None
    if not isinstance(evidence_result, dict):
        return records

    id_to_block: dict[str, int] = {}
    for record in _evidence_records(evidence_result) or []:
        candidate = record.get("candidate") or {}
        identifier = record.get("id") or candidate.get("id")
        try:
            block = int(candidate.get("block"))
        except (TypeError, ValueError):
            continue
        if identifier is not None:
            id_to_block[str(identifier)] = block

    filtered: list[dict[str, Any]] = []
    for record in records:
        candidates = [str(candidate) for candidate in record.get("candidates") or []]
        if any(id_to_block.get(candidate) == block_index for candidate in candidates):
            filtered.append(record)
    return filtered


def write_review_reports(
    book_dir: Path,
    reviews: list[dict[str, Any]],
    *,
    evidence_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Write one JSON/Markdown report per block plus JSON/Markdown summaries.

    ``evidence_result`` is the structured result from the review evidence layer,
    including verification candidates and arbitration conflicts. Only records
    belonging to each block are included in that block's report. The returned
    summary is also written to ``translation/review/summary.json`` and
    ``translation/review/summary.md``.
    """
    reports: list[dict[str, Any]] = []
    for block_index, review in enumerate(reviews):
        evidence = _filter_evidence_for_block(evidence_result, block_index)
        arbitrations = _filter_arbitrations_for_block(evidence_result, block_index)
        report = build_block_report(
            review,
            block_index=block_index,
            evidence=evidence,
            arbitrations=arbitrations,
        )
        write_block_report(book_dir, report)
        reports.append(report)

    summary = build_summary(reports)
    summary_json_path(book_dir).write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    summary_markdown_path(book_dir).write_text(render_summary_markdown(summary), encoding="utf-8")
    return summary


def read_legacy_proofread(path: Path) -> dict[str, Any]:
    """Read one legacy proofread Markdown report without modifying it."""
    path = Path(path)
    if not path.is_file():
        return {
            "kind": "legacy-proofread",
            "path": str(path),
            "verdict": "MISSING",
            "issues": 0,
            "complete": False,
            "raw": "",
        }
    text = path.read_text(encoding="utf-8")
    verdict = _legacy_verdict(path)
    return {
        "kind": "legacy-proofread",
        "path": str(path),
        "verdict": verdict,
        "issues": _legacy_issue_count(path),
        "complete": verdict in {PASS, FAIL},
        "raw": text,
    }


def read_legacy_summary(path: Path) -> dict[str, Any]:
    """Read a legacy proofread summary table without modifying it."""
    path = Path(path)
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    blocks: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    for match in _LEGACY_ROW_RE.finditer(text):
        chunk = match.group(1).strip()
        if chunk.lower() == "chunk":
            continue
        verdict = match.group(2)
        blocks.append(
            {
                "chunk": chunk,
                "verdict": verdict,
                "issues": int(match.group(3)),
            }
        )
        counts[verdict] = counts.get(verdict, 0) + 1
    return {
        "kind": "legacy-proofread-summary",
        "path": str(path),
        "blocks": blocks,
        "counts": counts,
        "raw": text,
    }


def read_report(path: Path) -> dict[str, Any]:
    """Read a v2 JSON report or a legacy proofread Markdown report.

    JSON reports are returned as stored. Markdown reports are normalized into the
    legacy record shape so completed works stay readable beside v2 artifacts.
    """
    path = Path(path)
    if path.suffix.lower() == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(f"review report is not a JSON object: {path}")
        return data
    return read_legacy_proofread(path)
