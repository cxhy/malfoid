#!/usr/bin/env python3
"""Apply confirmed review suggestions to segment state, one segment at a time.

Usage:
    .venv/bin/python tools/fix_review_issues.py works/<series>/books/book-01
    .venv/bin/python tools/fix_review_issues.py works/<series>/books/book-01 --dry-run

The tool reads confirmed issues from ``translation/review/block-*.json``,
groups them by chapter/segment, and asks the model for a corrected target for
each affected segment. Every call must return ``{"target": "..."}``. Invalid or
unsafe output is retried, then left unchanged and recorded in the fix log.
Segment count and order are never changed; only ``target`` is written.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import book_state
import segment_state
from batch_translate import relevant_glossary_terms
from model_client import call_client
from profile_rules import section

NO_ISSUE = re.compile(
    r"no issue|no change needed|no change|无需修改|不需要修改|无需改动|没有问题|无问题|无需调整",
    re.I,
)
NOTE_ONLY = re.compile(r"标注|注明|加注|译注|footnote", re.I)
FORBIDDEN = re.compile(r"达西|(?<!妮娅)德拉科(?!妮娅)")
ENGLISH = re.compile(r"[A-Za-z]{4,}")
MAX_RAW = 20000
AUTOFIX_CATEGORIES = frozenset(
    {
        "omission",
        "addition",
        "mistranslation",
        "semantic_reversal",
        "consistency",
        "terminology",
    }
)
STYLE_CATEGORIES = frozenset({"style"})
AUTOFIX_SEVERITIES = frozenset({"high", "medium"})
ALL_CATEGORIES = AUTOFIX_CATEGORIES | STYLE_CATEGORIES | frozenset({"other"})
CATEGORY_PATTERNS = (
    ("semantic_reversal", re.compile(r"语义反转|意思相反|反了|negation|reversal", re.I)),
    ("omission", re.compile(r"漏译|遗漏|漏掉|缺失|省略|丢失|omitted|missing", re.I)),
    ("addition", re.compile(r"增译|添加|多出|原文没有|added|extra", re.I)),
    ("mistranslation", re.compile(r"误译|错译|翻错|翻译错误|不准确|mistranslation|wrong", re.I)),
    ("terminology", re.compile(r"术语|译名|专名|term", re.I)),
    ("consistency", re.compile(r"漂移|不一致|前后不一|统一|称呼|inconsistent|drift", re.I)),
    ("style", re.compile(r"风格|语气|节奏|表达|措辞|润色|style|tone|rhythm", re.I)),
)
DEFAULT_SEVERITY = {
    "omission": "high",
    "addition": "high",
    "semantic_reversal": "high",
    "mistranslation": "high",
    "consistency": "medium",
    "terminology": "medium",
    "style": "low",
    "other": "medium",
}
ADVISORY_RE = re.compile(r"需确认|确认全书|核对|检查|check|confirm", re.I)


def classify_issue(issue: dict[str, Any]) -> tuple[str, str]:
    """Normalize an issue category/severity, inferring missing values from text."""
    category = str(issue.get("category") or "").strip().lower()
    severity = str(issue.get("severity") or "").strip().lower()
    text = " ".join(
        str(issue.get(field) or "") for field in ("subject", "message", "proposal")
    )
    if category not in ALL_CATEGORIES:
        for candidate, pattern in CATEGORY_PATTERNS:
            if pattern.search(text):
                category = candidate
                break
        else:
            category = "other"
    if severity not in {"high", "medium", "low"}:
        severity = DEFAULT_SEVERITY.get(category, "medium")
        if category in {"consistency", "terminology"} and ADVISORY_RE.search(text):
            severity = "low"
    return category, severity


def _glossary_variants(terms: list[Any] | None) -> list[str]:
    """Return forbidden glossary variants that are not substrings of canonical targets."""
    variants: list[str] = []
    seen: set[str] = set()
    for term in terms or []:
        if not isinstance(term, dict):
            continue
        canonical = str(term.get("target") or "")
        for variant in term.get("forbidden_targets") or []:
            if not isinstance(variant, str) or not variant.strip():
                continue
            value = variant.strip()
            if canonical and value in canonical:
                continue
            key = value.casefold()
            if key in seen:
                continue
            seen.add(key)
            variants.append(value)
    return variants


@dataclass
class FixTarget:
    chapter: int | float
    index: int
    source: str
    current: str
    issues: list[dict[str, Any]]
    review_target: str = ""


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def collect_targets(
    review_dir: Path, glossary_terms: list[Any] | None = None
) -> tuple[list[FixTarget], list[dict[str, Any]]]:
    """Return unique fix targets and skipped issue records.

    A confirmed issue whose review-time target contains a glossary forbidden
    variant is a deterministic terminology violation, so it is collected even
    when the model marked it low severity. Ordinary low-severity issues remain
    skipped.
    """
    by_key: dict[tuple[int | float, int], FixTarget] = {}
    skipped: list[dict[str, Any]] = []
    glossary_variants = _glossary_variants(glossary_terms)
    for path in sorted(review_dir.glob("block-*.json")):
        report = _read_json(path)
        evidence: dict[tuple[Any, Any, str], dict[str, Any]] = {}
        review_targets: dict[tuple[Any, Any], str] = {}
        for record in report.get("evidence") or []:
            candidate = record.get("candidate") or {}
            key = (
                candidate.get("chapter"),
                candidate.get("segment_index"),
                (candidate.get("message") or "")[:120],
            )
            evidence[key] = record
            for ref in record.get("evidence") or []:
                chapter = ref.get("chapter")
                index = ref.get("segment_index")
                target = ref.get("target")
                if chapter is None or index is None:
                    continue
                if not isinstance(target, str) or not target.strip():
                    continue
                review_targets[(chapter, index)] = target.strip()
        for issue in report.get("issues") or []:
            key = (
                issue.get("chapter"),
                issue.get("segment_index"),
                (issue.get("message") or "")[:120],
            )
            record = evidence.get(key)
            if not record or record.get("status") != "confirmed":
                continue
            text = f"{issue.get('message') or ''} {issue.get('proposal') or ''}"
            if NO_ISSUE.search(text):
                skipped.append({**issue, "reason": "no-issue observation"})
                continue
            if NOTE_ONLY.search(text):
                skipped.append({**issue, "reason": "note-only suggestion"})
                continue
            category, severity = classify_issue(issue)
            issue = {**issue, "category": category, "severity": severity}
            if category in STYLE_CATEGORIES:
                skipped.append({**issue, "reason": "style suggestion"})
                continue
            if category not in AUTOFIX_CATEGORIES:
                skipped.append(
                    {
                        **issue,
                        "reason": f"non-actionable category: {category}",
                    }
                )
                continue
            review_target_text = review_targets.get(
                (issue.get("chapter"), issue.get("segment_index")), ""
            )
            glossary_variant = next(
                (variant for variant in glossary_variants if variant in review_target_text),
                None,
            )
            if severity not in AUTOFIX_SEVERITIES and not glossary_variant:
                skipped.append({**issue, "reason": f"low severity: {severity}"})
                continue
            if glossary_variant:
                issue = {**issue, "glossary_variant": glossary_variant}
            chapter = segment_state.chapter_number(issue.get("chapter") or 0)
            index = int(issue.get("segment_index") or 0)
            target = by_key.get((chapter, index))
            if target is None:
                target = FixTarget(
                    chapter,
                    index,
                    "",
                    "",
                    [],
                    review_target=review_targets.get((issue.get("chapter"), issue.get("segment_index")), ""),
                )
                by_key[(chapter, index)] = target
            elif not target.review_target:
                target.review_target = review_targets.get(
                    (issue.get("chapter"), issue.get("segment_index")), ""
                )
            target.issues.append(issue)
    return list(by_key.values()), skipped


def load_segments(book_dir: Path, targets: list[FixTarget]) -> list[FixTarget]:
    """Attach source/current text from chapter state; drop missing segments."""
    loaded: list[FixTarget] = []
    by_chapter: dict[int | float, list[FixTarget]] = {}
    for target in targets:
        by_chapter.setdefault(target.chapter, []).append(target)
    for chapter_number, group in by_chapter.items():
        path = segment_state.chapter_path(book_dir, chapter_number)
        chapter = segment_state.load_chapter(path)
        segments = {int(segment.get("index", 0)): segment for segment in chapter["segments"]}
        for target in group:
            segment = segments.get(target.index)
            if segment is None:
                continue
            target.source = segment.get("source") or ""
            target.current = segment.get("target") or ""
            loaded.append(target)
    return loaded


def _glossary_text(terms: list[Any] | None) -> str:
    lines: list[str] = []
    for term in terms or []:
        if not isinstance(term, dict):
            continue
        source = term.get("source") or term.get("id") or ""
        target = term.get("target") or ""
        if not source and not target:
            continue
        line = f"- {source}"
        if target:
            line += f" => {target}"
        aliases = [alias for alias in term.get("aliases") or [] if isinstance(alias, str)]
        if aliases:
            line += f" (aliases: {', '.join(aliases)})"
        lines.append(line)
    return "\n".join(lines)


def build_fix_prompt(
    target: FixTarget, rules: str | None, glossary_terms: list[Any] | None = None
) -> str:
    issue_lines = []
    for issue in target.issues:
        issue_lines.append(f"- 问题：{issue.get('message') or ''}")
        category = issue.get("category") or ""
        severity = issue.get("severity") or ""
        if category or severity:
            issue_lines.append(f"  类别：{category or '未标注'}；严重度：{severity or '未标注'}")
        proposal = issue.get("proposal") or ""
        if proposal:
            issue_lines.append(f"  建议：{proposal}")
    lines = [
        "TASK: FIX REVIEW ISSUE",
        "只修改下面这一段的译文，保留原意、人物、情节和语气，不新增原文没有的内容。",
        "修正必须真正解决审查问题，不要机械照抄建议；建议只是参考。",
        "不要合并或拆分段落，不要写译注或说明。",
    ]
    if isinstance(rules, str) and rules.strip():
        lines.extend(["PROFILE RULES (must follow):", rules.strip(), ""])
    glossary = _glossary_text(
        relevant_glossary_terms(
            [{"source": target.source, "target": target.current}], glossary_terms
        )
    )
    if glossary.strip():
        lines.extend(["GLOSSARY (canonical targets, must be followed):", glossary, ""])
    lines.extend(
        [
            f"SOURCE:\n{target.source}",
            f"CURRENT TARGET:\n{target.current}",
            "REVIEW ISSUES:",
            *issue_lines,
            "",
            'Return only JSON: {"target": "修正后的中文译文"}',
        ]
    )
    return "\n".join(lines)


def parse_fix_output(raw: Any) -> str:
    text = raw if isinstance(raw, str) else str(raw)
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```[A-Za-z]*\s*\n?", "", stripped)
        stripped = re.sub(r"\n?```\s*$", "", stripped)
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", stripped, re.DOTALL)
        if not match:
            raise ValueError("fix output is not JSON")
        payload = json.loads(match.group(0))
    if not isinstance(payload, dict):
        raise ValueError("fix output must be a JSON object")
    target = payload.get("target")
    if not isinstance(target, str) or not target.strip():
        raise ValueError("fix output has no non-empty target")
    return target.strip()


def validate_fix(target: FixTarget, fixed: str) -> None:
    if FORBIDDEN.search(fixed):
        raise ValueError("fixed target contains a forbidden name")
    if ENGLISH.search(fixed):
        raise ValueError("fixed target contains English residue")
    source_len = len(target.source.strip())
    if source_len >= 20:
        ratio = len(fixed) / source_len
        if ratio < 0.1 or ratio > 1.2:
            raise ValueError(f"fixed target length ratio is unsafe: {ratio:.2f}")


def _issue_hash(target: FixTarget) -> str:
    blob = json.dumps(
        sorted(
            (issue.get("message") or "", issue.get("proposal") or "") for issue in target.issues
        ),
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _state_key(target: FixTarget) -> str:
    source_hash = segment_state.source_hash(target.source)
    return f"{target.chapter}:{target.index}:{source_hash}:{_issue_hash(target)}"


def _load_state(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"version": 1, "entries": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": 1, "entries": {}}
    if not isinstance(data, dict) or not isinstance(data.get("entries"), dict):
        return {"version": 1, "entries": {}}
    return data


def _save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def apply_fixes(
    book_dir: Path,
    targets: list[FixTarget],
    client: Any,
    *,
    rules: str | None = None,
    glossary_terms: list[Any] | None = None,
    retries: int = 2,
    dry_run: bool = False,
    state_path: Path | None = None,
) -> dict[str, Any]:
    """Fix each target and write it back; return counts and per-segment records."""
    state_path = state_path or book_dir / "work_temp" / "fix_review" / "state.json"
    state = _load_state(state_path)
    entries = state.setdefault("entries", {})
    records: list[dict[str, Any]] = []
    applied = failed = unchanged = skipped_applied = skipped_changed = planned = 0
    by_chapter: dict[int | float, list[FixTarget]] = {}
    for target in targets:
        by_chapter.setdefault(target.chapter, []).append(target)

    for chapter_number, group in sorted(by_chapter.items()):
        path = segment_state.chapter_path(book_dir, chapter_number)
        chapter = segment_state.load_chapter(path)
        segments = {int(segment.get("index", 0)): segment for segment in chapter["segments"]}
        changed = False
        if dry_run:
            for target in group:
                segment = segments.get(target.index)
                if segment is None:
                    records.append(
                        {
                            "chapter": chapter_number,
                            "index": target.index,
                            "status": "missing",
                        }
                    )
                    failed += 1
                    continue
                target.current = segment.get("target") or ""
                saved = entries.get(_state_key(target))
                if isinstance(saved, dict) and saved.get("target") == target.current.strip():
                    records.append(
                        {
                            "chapter": chapter_number,
                            "index": target.index,
                            "status": "already_applied",
                        }
                    )
                    skipped_applied += 1
                    continue
                if target.review_target.strip() and target.current.strip() != target.review_target.strip():
                    records.append(
                        {
                            "chapter": chapter_number,
                            "index": target.index,
                            "status": "changed_since_review",
                            "review_target": target.review_target,
                            "current": target.current,
                        }
                    )
                    skipped_changed += 1
                    continue
                records.append(
                    {
                        "chapter": chapter_number,
                        "index": target.index,
                        "status": "planned",
                        "source": target.source,
                        "current": target.current,
                        "issues": [
                            {
                                "message": issue.get("message") or "",
                                "proposal": issue.get("proposal") or "",
                                "category": issue.get("category"),
                                "severity": issue.get("severity"),
                            }
                            for issue in target.issues
                        ],
                    }
                )
                planned += 1
            continue
        for target in group:
            segment = segments.get(target.index)
            if segment is None:
                records.append(
                    {
                        "chapter": chapter_number,
                        "index": target.index,
                        "status": "missing",
                    }
                )
                failed += 1
                continue
            target.current = segment.get("target") or ""
            state_key = _state_key(target)
            saved = entries.get(state_key)
            if isinstance(saved, dict) and saved.get("target") == target.current.strip():
                records.append(
                    {
                        "chapter": chapter_number,
                        "index": target.index,
                        "status": "already_applied",
                    }
                )
                skipped_applied += 1
                continue
            if target.review_target.strip() and target.current.strip() != target.review_target.strip():
                records.append(
                    {
                        "chapter": chapter_number,
                        "index": target.index,
                        "status": "changed_since_review",
                        "review_target": target.review_target,
                        "current": target.current,
                    }
                )
                skipped_changed += 1
                continue
            fixed: str | None = None
            error = ""
            for attempt in range(retries + 1):
                raw = call_client(client, build_fix_prompt(target, rules, glossary_terms))
                try:
                    candidate = parse_fix_output(raw)
                    validate_fix(target, candidate)
                except Exception as exc:
                    error = str(exc) or exc.__class__.__name__
                    continue
                fixed = candidate
                break
            if fixed is None:
                records.append(
                    {
                        "chapter": chapter_number,
                        "index": target.index,
                        "status": "failed",
                        "error": error,
                        "source": target.source,
                        "current": target.current,
                        "issues": [
                            {
                                "message": issue.get("message") or "",
                                "proposal": issue.get("proposal") or "",
                            }
                            for issue in target.issues
                        ],
                        "raw": (raw if isinstance(raw, str) else str(raw))[:MAX_RAW],
                    }
                )
                failed += 1
                continue
            if fixed == target.current.strip():
                records.append(
                    {
                        "chapter": chapter_number,
                        "index": target.index,
                        "status": "unchanged",
                        "target": fixed,
                    }
                )
                unchanged += 1
                continue
            segment["target"] = fixed
            entries[state_key] = {"target": fixed}
            changed = True
            applied += 1
            records.append(
                {
                    "chapter": chapter_number,
                    "index": target.index,
                    "status": "applied",
                    "before": target.current,
                    "after": fixed,
                    "issues": [
                        {
                            "message": issue.get("message") or "",
                            "proposal": issue.get("proposal") or "",
                        }
                        for issue in target.issues
                    ],
                }
            )
        if changed and not dry_run:
            segment_state.save_chapter(path, chapter)
            _save_state(state_path, state)
    failed_records = [record for record in records if record.get("status") == "failed"]
    queue_dir = book_dir / "work_temp" / "fix_review"
    queue_json = queue_md = None
    if not dry_run:
        queue_json = queue_dir / "manual_queue.json"
        queue_md = queue_dir / "manual_queue.md"
        if failed_records:
            queue_dir.mkdir(parents=True, exist_ok=True)
            queue_json.write_text(
                json.dumps(failed_records, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            lines = ["# Manual fix queue", ""]
            for record in failed_records:
                lines.append(f"## ch{record['chapter']}:{record['index']}")
                lines.append(f"SOURCE: {record.get('source') or ''}")
                lines.append(f"CURRENT: {record.get('current') or ''}")
                lines.append(f"ERROR: {record.get('error') or ''}")
                for issue in record.get("issues") or []:
                    lines.append(f"ISSUE: {issue.get('message') or ''}")
                    if issue.get("proposal"):
                        lines.append(f"PROPOSAL: {issue['proposal']}")
                lines.append("")
            queue_md.write_text("\n".join(lines) + "\n", encoding="utf-8")
        else:
            for path in (queue_json, queue_md):
                if path.is_file():
                    path.unlink()
    return {
        "applied": applied,
        "failed": failed,
        "unchanged": unchanged,
        "skipped_applied": skipped_applied,
        "skipped_changed": skipped_changed,
        "planned": planned,
        "records": records,
        "manual_queue_json": str(queue_json) if queue_json is not None else None,
        "manual_queue_md": str(queue_md) if queue_md is not None else None,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("book_dir", type=Path)
    parser.add_argument(
        "--review-dir",
        type=Path,
        help="review report directory (default: <book>/translation/review)",
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("MALFOID_MODEL", "DeepSeek-V4.1-Flash-1017-TensorFold-Zig"),
    )
    parser.add_argument("--effort", default="low")
    parser.add_argument("--max-tokens", type=int, default=8000)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--strict", action="store_true", help="exit non-zero when any segment fails"
    )
    parser.add_argument("--limit", type=int, default=0, help="fix at most N segments")
    args = parser.parse_args()

    book_dir = args.book_dir
    review_dir = args.review_dir or book_dir / "translation" / "review"
    if not review_dir.is_dir():
        raise SystemExit(f"review directory not found: {review_dir}")

    glossary_terms = book_state.load_context_terms(book_dir)
    targets, skipped = collect_targets(review_dir, glossary_terms=glossary_terms)
    targets = load_segments(book_dir, targets)
    if args.limit:
        targets = targets[: args.limit]
    if not targets:
        print(json.dumps({"targets": 0, "skipped": len(skipped)}, ensure_ascii=False))
        return

    from model_api import make_client

    client = make_client(
        args.model,
        args.effort,
        args.max_tokens,
        usage_log=book_dir / "work_temp" / "usage.jsonl",
    )
    rules = section("translation", work_dir=book_dir)
    started = time.time()
    result = apply_fixes(
        book_dir,
        targets,
        client,
        rules=rules,
        glossary_terms=glossary_terms,
        retries=args.retries,
        dry_run=args.dry_run,
    )
    result["targets"] = len(targets)
    result["skipped"] = skipped
    result["dry_run"] = args.dry_run
    result["elapsed"] = round(time.time() - started, 1)
    log_dir = book_dir / "work_temp" / "fix_review"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"fix-{int(time.time())}.json"
    log_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "targets": len(targets),
                "applied": result["applied"],
                "planned": result["planned"],
                "failed": result["failed"],
                "unchanged": result["unchanged"],
                "skipped_applied": result["skipped_applied"],
                "skipped_changed": result["skipped_changed"],
                "skipped": len(skipped),
                "dry_run": args.dry_run,
                "elapsed": result["elapsed"],
                "log": str(log_path),
                "manual_queue": result.get("manual_queue_md"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    if args.strict and result["failed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
