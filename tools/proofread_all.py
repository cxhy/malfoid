#!/usr/bin/env python3
"""Proofread every pending chunk of a work, independently of the translator.

Each chunk is a separate model call with only the English source, the Chinese
translation and the glossary in context, so one chunk's judgement cannot leak
into another's. Reports go to <work>/translation/proofread/<chunk>.md; finished
reports are skipped on rerun, which makes long runs resumable.

Usage:
    .venv/bin/python tools/proofread_all.py works/<work> [chunk0001 ...]
    .venv/bin/python tools/proofread_all.py works/<work> --start 1 --end 21
    .venv/bin/python tools/proofread_all.py works/<work> --workers 1 --effort medium

The driver never edits a translation; it only writes reports and a summary.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = str(ROOT / ".venv/bin/python")
VERDICT_RE = re.compile(r"结论[:：]\s*(PASS|FAIL)")


def chunk_ids(work: Path) -> list[str]:
    temp = work / "work_temp"
    return sorted(p.stem for p in temp.glob("chunk*.md") if not p.name.startswith("output_"))


def pending(work: Path) -> list[str]:
    done = {p.stem for p in (work / "translation" / "proofread").glob("chunk*.md")}
    return [cid for cid in chunk_ids(work) if cid not in done]


def verdict_of(path: Path) -> str:
    if not path.is_file():
        return "MISSING"
    m = VERDICT_RE.search(path.read_text(encoding="utf-8"))
    return m.group(1) if m else "UNKNOWN"


def proofread(
    work: Path, cid: str, effort: str, source: str, force: bool = False
) -> tuple[str, str]:
    cmd = [
        PY,
        str(ROOT / "tools/proofread_chunk.py"),
        str(work),
        cid,
        "--effort",
        effort,
        "--source",
        source,
    ]
    if force:
        cmd.append("--force")
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
    )
    report = work / "translation" / "proofread" / f"{cid}.md"
    if proc.returncode != 0:
        tail = (proc.stdout + proc.stderr).strip().splitlines()[-1:] or [""]
        return cid, f"ERROR {tail[0]}"
    return cid, verdict_of(report)


def issue_count(path: Path) -> int:
    if not path.is_file():
        return 0
    return len(re.findall(r"(?m)^- 【[^】]*】", path.read_text(encoding="utf-8")))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("work_dir", type=Path)
    ap.add_argument("chunks", nargs="*")
    ap.add_argument("--start", type=int, help="first chunk number, 1-based inclusive")
    ap.add_argument("--end", type=int, help="last chunk number, 1-based inclusive")
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--effort", default="low")
    ap.add_argument("--source", choices=["auto", "polished", "draft"], default="auto")
    ap.add_argument("--force", action="store_true", help="redo chunks that already have a report")
    ap.add_argument("--summary", help="summary file name under translation/proofread/")
    args = ap.parse_args()

    work = args.work_dir
    all_ids = chunk_ids(work)
    if args.chunks:
        todo = args.chunks
    elif args.start or args.end:
        lo = args.start or 1
        hi = args.end or len(all_ids)
        todo = all_ids[lo - 1 : hi]
    else:
        todo = all_ids
    if not args.force:
        done = {p.stem for p in (work / "translation" / "proofread").glob("chunk*.md")}
        todo = [c for c in todo if c not in done]
    if not todo:
        print("nothing to proofread")
        return

    print(f"proofreading {len(todo)} chunk(s) with {args.workers} worker(s)", flush=True)
    results: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(proofread, work, cid, args.effort, args.source, args.force): cid
            for cid in todo
        }
        for n, fut in enumerate(as_completed(futures), 1):
            cid, verdict = fut.result()
            results[cid] = verdict
            issues = issue_count(work / "translation" / "proofread" / f"{cid}.md")
            print(f"[{n}/{len(todo)}] {cid}: {verdict} issues={issues}", flush=True)

    counts: dict[str, int] = {}
    for v in results.values():
        counts[v.split()[0]] = counts.get(v.split()[0], 0) + 1
    print("summary:", ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    bad = [c for c, v in sorted(results.items()) if not v.startswith("PASS")]
    if bad:
        print("needs attention:", ", ".join(bad))

    summary = args.summary or (
        f"summary-{args.start or 1}-{args.end or len(all_ids)}.md"
        if (args.start or args.end)
        else "summary.md"
    )
    dest = work / "translation" / "proofread" / summary
    lines = [f"# 校对汇总 {args.start or 1}–{args.end or len(all_ids)}", ""]
    lines.append(f"- 块数：{len(results)}")
    for k, v in sorted(counts.items()):
        lines.append(f"- {k}：{v}")
    lines.append("")
    lines.append("| chunk | 结论 | 问题数 |")
    lines.append("|---|---|---|")
    for cid in sorted(results):
        issues = issue_count(work / "translation" / "proofread" / f"{cid}.md")
        lines.append(f"| {cid} | {results[cid]} | {issues} |")
    dest.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("summary written:", dest)
    if bad:
        sys.exit(1)


if __name__ == "__main__":
    main()
