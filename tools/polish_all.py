#!/usr/bin/env python3
"""Polish every pending chunk of a work with the local provider.

Usage:
    .venv/bin/python tools/polish_all.py works/<work> [chunk0001 ...]

Runs tools/polish_chunk.py serially (the local provider serializes requests).
Each chunk gets up to four attempts: twice whole, then split in 2 and 3 parts.
Splitting is needed because this model's reasoning tokens count toward
max_tokens, so a long chunk can spend the whole budget thinking and return
empty content. Polished files smaller than MIN_BYTES are treated as pending.

The original translation in work_temp/output_chunk*.md is never modified;
polished chunks go to translation/polished/chunkXXXX.md.
"""

from __future__ import annotations

import argparse
import subprocess
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = str(ROOT / ".venv/bin/python")
MIN_BYTES = 50


def is_done(path: Path) -> bool:
    return path.is_file() and path.stat().st_size >= MIN_BYTES


def pending_chunks(work: Path) -> list[str]:
    temp = work / "work_temp"
    polished = work / "translation" / "polished"
    out = []
    for p in sorted(temp.glob("output_chunk*.md")):
        cid = p.stem.removeprefix("output_")
        if not is_done(polished / f"{cid}.md"):
            out.append(cid)
    return out


def polish(work: Path, cid: str, effort: str, max_tokens: int, split: int) -> tuple[bool, str]:
    cmd = [
        PY,
        str(ROOT / "tools/polish_chunk.py"),
        str(work),
        cid,
        "--effort",
        effort,
        "--max-tokens",
        str(max_tokens),
    ]
    if split > 1:
        cmd += ["--split", str(split)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    dest = work / "translation" / "polished" / f"{cid}.md"
    ok = proc.returncode == 0 and is_done(dest)
    msg = (proc.stdout + proc.stderr).strip().replace("\n", " | ")
    return ok, msg


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("work_dir", type=Path)
    ap.add_argument("chunks", nargs="*")
    ap.add_argument(
        "--effort",
        default="minimal",
        help="minimal reasoning leaves the output budget for prose; medium/high overflow max_tokens",
    )
    ap.add_argument(
        "--max-tokens",
        type=int,
        default=32768,
        help="reasoning tokens count toward this; 16000 can leave empty content",
    )
    ap.add_argument(
        "--attempt-ladder",
        default="1,1,2,3",
        help="comma-separated split counts, one per attempt (default 1,1,2,3)",
    )
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    work = args.work_dir
    todo = args.chunks or pending_chunks(work)
    if args.force:
        for cid in todo:
            (work / "translation" / "polished" / f"{cid}.md").unlink(missing_ok=True)
    todo = [c for c in todo if not is_done(work / "translation" / "polished" / f"{c}.md")]
    if not todo:
        print("nothing to polish")
        return

    log_dir = work / "logs"
    log_dir.mkdir(exist_ok=True)
    log_path = log_dir / f"polish_{datetime.now().strftime('%Y%m%d-%H%M%S')}.log"
    log = open(log_path, "a", encoding="utf-8")

    def emit(line: str) -> None:
        print(line, flush=True)
        log.write(line + "\n")
        log.flush()

    emit(f"polishing {len(todo)} chunk(s), effort={args.effort}, max_tokens={args.max_tokens}")
    failures: list[str] = []
    for n, cid in enumerate(todo, 1):
        ok, msg = False, ""
        ladder = [int(x) for x in args.attempt_ladder.split(",") if x.strip()]
        for attempt, split in enumerate(ladder, 1):
            ok, msg = polish(work, cid, args.effort, args.max_tokens, split)
            if ok:
                break
            emit(f"[{n}/{len(todo)}] {cid}: attempt {attempt} split={split} failed ({msg})")
        emit(f"[{n}/{len(todo)}] {cid}: {'ok' if ok else 'FAIL'} {msg}")
        if not ok:
            failures.append(cid)

    emit("done" if not failures else f"failed: {', '.join(failures)}")
    log.close()


if __name__ == "__main__":
    main()
