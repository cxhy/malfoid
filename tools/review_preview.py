#!/usr/bin/env python3
"""Preview review validation and Book 1 go/no-go decision (pipeline v2, ticket 11).

The migrated two-block preview is reviewed end-to-end through the structured
review architecture. Blocks are planned once over the whole book as one contiguous
sequence; each segment carries an unambiguous global review index, and issues are
mapped back to chapter/local positions before evidence verification. Reports are
written under the preview book's ``translation/review`` area.

Before evidence verification every reported issue is checked against the segment
state. An issue that does not trace to an aligned source/target pair is dropped
and recorded as an alignment false positive, so no report can contain an issue
caused by English/Chinese segment misalignment. The run records a false-positive
rate and a deterministic GO/NO-GO decision for Book 1 mass translation.

Review stays advisory. This module reads chapter state and writes review reports;
it never edits formal text.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any

import preview_migration
import review_blocks
import review_book_v2
import review_evidence
import review_reports
import segment_state

ACCEPTANCE_JSON = "acceptance.json"
ACCEPTANCE_MARKDOWN = "acceptance.md"
ACCEPTANCE_VERSION = 1
DEFAULT_MAX_FALSE_POSITIVE_RATE = 0.5

_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
_LATIN_RE = re.compile(r"[A-Za-z]")


def _position(chapter: int, index: int, reason: str) -> dict[str, Any]:
    return {
        "chapter": chapter,
        "segment_index": index,
        "ref": f"ch-{chapter:02d}:{index}",
        "reason": reason,
    }


def check_alignment(
    chapters: list[dict[str, Any]],
    reuse_map: dict[str, dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Check every segment's source/target pair for alignment failures.

    A segment is misaligned when it has no target, its stored source hash no
    longer matches the source text, its English source carries a target without
    any Chinese text, or a reused target disagrees with the target indexed for
    that source hash. Every failure names a concrete chapter and segment position.
    """
    missing_targets: list[dict[str, Any]] = []
    hash_mismatches: list[dict[str, Any]] = []
    non_chinese_targets: list[dict[str, Any]] = []
    reuse_mismatches: list[dict[str, Any]] = []
    misaligned: dict[tuple[int, int], dict[str, Any]] = {}
    checked = 0

    def mark(chapter: int, index: int, reason: str) -> dict[str, Any]:
        entry = misaligned.get((chapter, index))
        if entry is None:
            entry = _position(chapter, index, reason)
            misaligned[(chapter, index)] = entry
        elif reason not in entry["reason"].split("; "):
            entry["reason"] += f"; {reason}"
        return entry

    for position, chapter in enumerate(chapters):
        number = int(chapter.get("chapter") or position + 1)
        for segment in chapter.get("segments") or []:
            checked += 1
            index = int(segment.get("index", -1))
            source = segment.get("source") or ""
            target = segment.get("target")
            stored_hash = segment.get("source_hash")

            if stored_hash and source:
                actual_hash = segment_state.source_hash(source)
                if stored_hash != actual_hash:
                    entry = mark(number, index, "source hash mismatch")
                    hash_mismatches.append(entry)

            if not isinstance(target, str) or not target.strip():
                entry = mark(number, index, "missing target")
                missing_targets.append(entry)
                continue

            if _LATIN_RE.search(source) and not _CJK_RE.search(target):
                entry = mark(number, index, "non-Chinese target")
                non_chinese_targets.append(entry)

            if reuse_map:
                origin = segment.get("target_origin")
                if origin in {"polished", "draft"}:
                    key = stored_hash or segment_state.source_hash(source)
                    expected = reuse_map.get(key)
                    if expected is not None and expected.get("target") != target:
                        entry = mark(number, index, "reused target hash mismatch")
                        reuse_mismatches.append(entry)

    ordered = [misaligned[key] for key in sorted(misaligned)]
    return {
        "checked": checked,
        "clean": not ordered,
        "missing_targets": missing_targets,
        "hash_mismatches": hash_mismatches,
        "non_chinese_targets": non_chinese_targets,
        "reuse_mismatches": reuse_mismatches,
        "misaligned": ordered,
    }


def load_reuse_map(book_dir: Path) -> dict[str, dict[str, str]] | None:
    """Load the legacy source-hash reuse map recorded by preview migration."""
    book = Path(book_dir)
    report_path = book / "work_temp" / preview_migration.MIGRATION_REPORT
    if not report_path.is_file():
        return None
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    chunk_ids = report.get("chunk_ids") or []
    if not chunk_ids:
        return None
    try:
        reuse, _ = preview_migration.build_reuse_map(book.parent, chunk_ids)
    except (OSError, ValueError):
        return None
    return reuse or None


def _misaligned_keys(alignment: dict[str, Any]) -> set[tuple[int, int]]:
    return {
        (int(item["chapter"]), int(item["segment_index"]))
        for item in alignment.get("misaligned") or []
    }


def _map_and_filter_reviews(
    reviews: list[dict[str, Any]],
    global_map: dict[int, tuple[int, int]],
    misaligned_keys: set[tuple[int, int]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Map global review indices to chapter/local positions and drop misaligned issues."""
    mapped, invalid = review_book_v2.map_reviews(reviews, global_map)
    cleaned: list[dict[str, Any]] = []
    for review in mapped:
        issues: list[dict[str, Any]] = []
        for issue in review.get("issues") or []:
            chapter = int(issue["chapter"])
            index = int(issue["segment_index"])
            if (chapter, index) in misaligned_keys:
                invalid.append(
                    {
                        **_position(chapter, index, "misaligned anchor"),
                        "message": issue.get("message") or "",
                        "global_segment_index": issue.get("global_segment_index"),
                    }
                )
                continue
            issues.append(issue)
        copy = dict(review)
        copy["issues"] = issues
        cleaned.append(copy)
    return cleaned, invalid


def _filter_candidates(
    candidates: list[dict[str, Any]],
    global_map: dict[int, tuple[int, int]],
    misaligned_keys: set[tuple[int, int]],
) -> list[dict[str, Any]]:
    """Map global candidates and keep only aligned positions."""
    mapped, _ = review_book_v2.map_candidates(candidates, global_map)
    kept: list[dict[str, Any]] = []
    for candidate in mapped:
        chapter = int(candidate["chapter"])
        index = int(candidate["segment_index"])
        if (chapter, index) in misaligned_keys:
            continue
        kept.append(candidate)
    return kept


def compute_metrics(
    reviews: list[dict[str, Any]],
    evidence: dict[str, Any],
    invalid_issues: list[dict[str, Any]],
    alignment: dict[str, Any],
) -> dict[str, Any]:
    """Compute traceability and false-positive metrics for one preview run."""
    candidates = [record for record in evidence.get("candidates") or [] if isinstance(record, dict)]
    statuses = [record.get("status") for record in candidates]
    confirmed = statuses.count("confirmed")
    rejected = statuses.count("rejected")
    unresolved = statuses.count("unresolved")
    decided = confirmed + rejected
    valid_issues = len(candidates)
    total_issues = valid_issues + len(invalid_issues)
    alignment_false_positives = sum(
        1 for issue in invalid_issues if issue.get("reason") == "misaligned anchor"
    )
    return {
        "blocks": len(reviews),
        "reviewed_segments": sum(int(review.get("reviewed_segments") or 0) for review in reviews),
        "complete": bool(reviews) and all(review.get("complete") is True for review in reviews),
        "issues": total_issues,
        "valid_issues": valid_issues,
        "untraceable_issues": len(invalid_issues),
        "alignment_false_positives": alignment_false_positives,
        "alignment_false_positive_rate": (
            alignment_false_positives / total_issues if total_issues else 0.0
        ),
        "confirmed": confirmed,
        "rejected": rejected,
        "unresolved": unresolved,
        "decided": decided,
        "false_positive_rate": rejected / decided if decided else 0.0,
        "false_positive_rate_basis": "rejected / decided issues",
        "unresolved_conflicts": len(evidence.get("unresolved_conflicts") or []),
        "alignment_clean": alignment.get("clean") is True,
    }


def decide_go_no_go(
    metrics: dict[str, Any],
    alignment: dict[str, Any],
    *,
    max_false_positive_rate: float = DEFAULT_MAX_FALSE_POSITIVE_RATE,
) -> dict[str, Any]:
    """Return the deterministic Book 1 go/no-go decision for a preview run."""
    if not 0.0 <= max_false_positive_rate <= 1.0:
        raise ValueError("max_false_positive_rate must be between 0 and 1")

    reasons: list[str] = []
    if not metrics.get("complete"):
        reasons.append("review blocks are incomplete")
    if alignment.get("clean") is not True:
        reasons.append("segment alignment check failed")
    if metrics.get("untraceable_issues"):
        reasons.append("some issues do not trace to aligned source/target positions")
    rate = float(metrics.get("false_positive_rate") or 0.0)
    if rate > max_false_positive_rate:
        reasons.append(
            f"false-positive rate {rate:.2f} exceeds the threshold {max_false_positive_rate:.2f}"
        )

    decision = "GO" if not reasons else "NO-GO"
    return {
        "decision": decision,
        "reasons": reasons,
        "max_false_positive_rate": max_false_positive_rate,
        "human_review_required": bool(
            metrics.get("unresolved") or metrics.get("unresolved_conflicts")
        ),
    }


def _acceptance_markdown(result: dict[str, Any]) -> str:
    metrics = result["metrics"]
    decision = result["decision"]
    alignment = result["alignment"]
    lines = [
        "# 试译审查验收",
        "",
        f"- 书目录：{result['book_dir']}",
        f"- 审查块：{metrics['blocks']}",
        f"- 已审段落：{metrics['reviewed_segments']}",
        f"- 完整：{'是' if metrics['complete'] else '否'}",
        f"- 问题数：{metrics['issues']}",
        f"- 可追溯问题：{metrics['valid_issues']}",
        f"- 不可追溯问题：{metrics['untraceable_issues']}",
        f"- 对齐检查：{'通过' if alignment['clean'] else '失败'}",
        f"- 对齐误报：{metrics['alignment_false_positives']}",
        f"- 证据确认：{metrics['confirmed']}",
        f"- 证据驳回：{metrics['rejected']}",
        f"- 待人工：{metrics['unresolved']}",
        f"- 内容误报率：{metrics['false_positive_rate']:.1%}（驳回/已裁决）",
        f"- 对齐误报率：{metrics['alignment_false_positive_rate']:.1%}（对齐误报/全部问题）",
        f"- Book 1 结论：{decision['decision']}",
    ]
    if decision.get("human_review_required"):
        lines.append("- 需人工复核：是")
    if decision.get("reasons"):
        lines.extend(["", "## 结论依据", ""])
        for reason in decision["reasons"]:
            lines.append(f"- {reason}")
    lines.extend(["", "> 审查仅提供建议，未修改正文。", ""])
    return "\n".join(lines)


def write_acceptance(book_dir: Path, result: dict[str, Any]) -> tuple[Path, Path]:
    """Write the preview acceptance record under the review area."""
    directory = review_reports.review_dir(book_dir)
    directory.mkdir(parents=True, exist_ok=True)
    json_path = directory / ACCEPTANCE_JSON
    markdown_path = directory / ACCEPTANCE_MARKDOWN
    record = {
        "version": ACCEPTANCE_VERSION,
        "kind": "preview-review-acceptance",
        "advisory": True,
        "book_dir": str(book_dir),
        "alignment": result["alignment"],
        "metrics": result["metrics"],
        "decision": result["decision"],
        "invalid_issues": result["invalid_issues"],
        "summary": {
            "verdict": result["summary"].get("verdict"),
            "counts": result["summary"].get("counts"),
        },
    }
    json_path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    markdown_path.write_text(_acceptance_markdown(result), encoding="utf-8")
    return json_path, markdown_path


def run_preview_review(
    book_dir: Path,
    client: Any,
    *,
    budget: int = review_blocks.DEFAULT_REVIEW_BUDGET,
    workers: int = review_blocks.DEFAULT_WORKERS,
    max_bisect_depth: int = review_blocks.DEFAULT_BISECT_DEPTH,
    max_segment_retries: int = review_blocks.DEFAULT_SEGMENT_RETRIES,
    measure=None,
    cache_dir: Path | None = None,
    reuse_map: dict[str, dict[str, str]] | None = None,
    max_false_positive_rate: float = DEFAULT_MAX_FALSE_POSITIVE_RATE,
) -> dict[str, Any]:
    """Review a migrated preview book and record the Book 1 decision.

    The client is injected, so tests can run the whole path offline. Reports are
    written under ``translation/review``; formal chapter state is never modified.
    """
    book = Path(book_dir)
    chapters = preview_migration.load_chapters(book)
    if not chapters:
        raise ValueError(f"preview book has no chapter state: {book}")
    if reuse_map is None:
        reuse_map = load_reuse_map(book)

    alignment = check_alignment(chapters, reuse_map)
    segments, global_map = review_book_v2.flatten_segments(chapters)
    blocks = review_blocks.plan_blocks(segments, budget, measure)
    reviews = review_blocks.run_review(
        client,
        segments,
        budget=budget,
        workers=workers,
        max_bisect_depth=max_bisect_depth,
        max_segment_retries=max_segment_retries,
        measure=measure,
        cache_dir=cache_dir,
    )
    if len(reviews) != len(blocks):
        raise RuntimeError("review block planning changed between plan and run")
    candidates = review_evidence.collect_candidates(reviews, blocks)
    misaligned_keys = _misaligned_keys(alignment)
    reviews, invalid_issues = _map_and_filter_reviews(reviews, global_map, misaligned_keys)
    candidates = _filter_candidates(candidates, global_map, misaligned_keys)
    evidence = review_book_v2.verify_and_arbitrate(client, candidates, chapters)
    metrics = compute_metrics(reviews, evidence, invalid_issues, alignment)
    decision = decide_go_no_go(
        metrics,
        alignment,
        max_false_positive_rate=max_false_positive_rate,
    )
    summary = review_reports.write_review_reports(book, reviews, evidence_result=evidence)

    result: dict[str, Any] = {
        "book_dir": str(book),
        "advisory": True,
        "alignment": alignment,
        "segments": segments,
        "blocks": blocks,
        "reviews": reviews,
        "evidence": evidence,
        "metrics": metrics,
        "decision": decision,
        "invalid_issues": invalid_issues,
        "summary": summary,
    }
    json_path, markdown_path = write_acceptance(book, result)
    result["acceptance"] = {"json": str(json_path), "markdown": str(markdown_path)}
    return result


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("book_dir", type=Path, help="preview book sub-work directory")
    parser.add_argument(
        "--model",
        default=os.environ.get("MALFOID_MODEL", "DeepSeek-V4.1-Flash-1017-TensorFold-Zig"),
    )
    parser.add_argument("--effort", default="low")
    parser.add_argument("--max-tokens", type=int, default=24000)
    parser.add_argument("--budget", type=int, default=review_blocks.DEFAULT_REVIEW_BUDGET)
    parser.add_argument("--workers", type=int, default=review_blocks.DEFAULT_WORKERS)
    parser.add_argument("--max-bisect-depth", type=int, default=review_blocks.DEFAULT_BISECT_DEPTH)
    parser.add_argument(
        "--max-segment-retries", type=int, default=review_blocks.DEFAULT_SEGMENT_RETRIES
    )
    parser.add_argument(
        "--max-false-positive-rate", type=float, default=DEFAULT_MAX_FALSE_POSITIVE_RATE
    )
    parser.add_argument("--cache-dir", type=Path, help="review block cache directory")
    parser.add_argument("--no-cache", action="store_true")
    args = parser.parse_args(argv)

    cache_dir = None
    if not args.no_cache:
        cache_dir = args.cache_dir or args.book_dir / "work_temp" / "review_cache"
    client = review_book_v2.ReviewModelClient(args.model, args.effort, args.max_tokens)
    result = run_preview_review(
        args.book_dir,
        client,
        budget=args.budget,
        workers=args.workers,
        max_bisect_depth=args.max_bisect_depth,
        max_segment_retries=args.max_segment_retries,
        cache_dir=cache_dir,
        max_false_positive_rate=args.max_false_positive_rate,
    )
    summary = {
        "metrics": result["metrics"],
        "decision": result["decision"],
        "alignment": {
            "clean": result["alignment"]["clean"],
            "misaligned": result["alignment"]["misaligned"],
        },
        "summary": result["summary"],
        "acceptance": result["acceptance"],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
