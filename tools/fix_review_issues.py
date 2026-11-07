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

import segment_state
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


@dataclass
class FixTarget:
    chapter: int
    index: int
    source: str
    current: str
    issues: list[dict[str, Any]]


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def collect_targets(review_dir: Path) -> tuple[list[FixTarget], list[dict[str, Any]]]:
    """Return unique fix targets and skipped issue records."""
    by_key: dict[tuple[int, int], FixTarget] = {}
    skipped: list[dict[str, Any]] = []
    for path in sorted(review_dir.glob("block-*.json")):
        report = _read_json(path)
        evidence: dict[tuple[Any, Any, str], dict[str, Any]] = {}
        for record in report.get("evidence") or []:
            candidate = record.get("candidate") or {}
            key = (
                candidate.get("chapter"),
                candidate.get("segment_index"),
                (candidate.get("message") or "")[:120],
            )
            evidence[key] = record
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
            chapter = int(issue.get("chapter") or 0)
            index = int(issue.get("segment_index") or 0)
            target = by_key.get((chapter, index))
            if target is None:
                target = FixTarget(chapter, index, "", "", [])
                by_key[(chapter, index)] = target
            target.issues.append(issue)
    return list(by_key.values()), skipped


def load_segments(book_dir: Path, targets: list[FixTarget]) -> list[FixTarget]:
    """Attach source/current text from chapter state; drop missing segments."""
    loaded: list[FixTarget] = []
    by_chapter: dict[int, list[FixTarget]] = {}
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


def build_fix_prompt(target: FixTarget, rules: str | None) -> str:
    issue_lines = []
    for issue in target.issues:
        issue_lines.append(f"- 问题：{issue.get('message') or ''}")
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
    current_len = max(1, len(target.current))
    ratio = len(fixed) / current_len
    if ratio < 0.4 or ratio > 2.0:
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
    retries: int = 2,
    dry_run: bool = False,
    state_path: Path | None = None,
) -> dict[str, Any]:
    """Fix each target and write it back; return counts and per-segment records."""
    state_path = state_path or book_dir / "work_temp" / "fix_review" / "state.json"
    state = _load_state(state_path)
    entries = state.setdefault("entries", {})
    records: list[dict[str, Any]] = []
    applied = failed = unchanged = skipped_applied = 0
    by_chapter: dict[int, list[FixTarget]] = {}
    for target in targets:
        by_chapter.setdefault(target.chapter, []).append(target)

    for chapter_number, group in sorted(by_chapter.items()):
        path = segment_state.chapter_path(book_dir, chapter_number)
        chapter = segment_state.load_chapter(path)
        segments = {int(segment.get("index", 0)): segment for segment in chapter["segments"]}
        changed = False
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
            fixed: str | None = None
            error = ""
            for attempt in range(retries + 1):
                raw = call_client(client, build_fix_prompt(target, rules))
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
    return {
        "applied": applied,
        "failed": failed,
        "unchanged": unchanged,
        "skipped_applied": skipped_applied,
        "records": records,
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

    targets, skipped = collect_targets(review_dir)
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
                "failed": result["failed"],
                "unchanged": result["unchanged"],
                "skipped_applied": result["skipped_applied"],
                "skipped": len(skipped),
                "dry_run": args.dry_run,
                "elapsed": result["elapsed"],
                "log": str(log_path),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    if args.strict and result["failed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
