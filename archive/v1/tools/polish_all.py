#!/usr/bin/env python3
"""Polish every pending chunk of a work with the local provider.

Usage:
    .venv/bin/python tools/polish_all.py works/<work> [chunk0001 ...]

Runs tools/polish_chunk.py serially (the local provider serializes requests).
Each chunk gets up to four attempts: whole, then split in 2, 4 and 8 parts.
Splitting is needed because this model's reasoning tokens count toward
max_tokens, so a long chunk can spend the whole budget thinking and return
empty content. Polished files smaller than MIN_BYTES are treated as pending;
``--recheck`` also validates existing files and retries structurally broken
ones. A failed attempt removes its output so a later resume cannot mistake a
broken file for finished work.

The original translation in work_temp/output_chunk*.md is never modified;
polished chunks go to translation/polished/chunkXXXX.md.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = str(ROOT / ".venv/bin/python")
import polish_chunk  # noqa: E402

MIN_BYTES = 50


def is_done(path: Path) -> bool:
    return path.is_file() and path.stat().st_size >= MIN_BYTES


def valid(work: Path, cid: str) -> bool:
    """True when a polished chunk exists and passes the structural checks."""
    dest = work / "translation" / "polished" / f"{cid}.md"
    if not is_done(dest):
        return False
    temp = work / "work_temp"
    old_path = temp / f"output_{cid}.md"
    glossary_path = temp / "glossary.json"
    if not old_path.is_file() or not glossary_path.is_file():
        return True
    try:
        terms = json.loads(glossary_path.read_text(encoding="utf-8"))["terms"]
    except (json.JSONDecodeError, KeyError):
        return True
    return not polish_chunk.check(
        old_path.read_text(encoding="utf-8"),
        dest.read_text(encoding="utf-8"),
        terms,
    )


def all_chunks(work: Path) -> list[str]:
    temp = work / "work_temp"
    return sorted(p.stem.removeprefix("output_") for p in temp.glob("output_chunk*.md"))


def pending_chunks(work: Path) -> list[str]:
    polished = work / "translation" / "polished"
    return [cid for cid in all_chunks(work) if not is_done(polished / f"{cid}.md")]


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
    if ok and not valid(work, cid):
        ok = False
    msg = (proc.stdout + proc.stderr).strip().replace("\n", " | ")
    if ok:
        return True, msg
    # Never leave a structurally broken output behind: a size-only resume would
    # treat it as finished and skip it forever.
    dest.unlink(missing_ok=True)
    return False, msg


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
        default="1,2,4,8",
        help="comma-separated split counts, one per attempt (default 1,2,4,8)",
    )
    ap.add_argument("--force", action="store_true")
    ap.add_argument(
        "--recheck",
        action="store_true",
        help="validate existing polished chunks and retry structurally broken ones",
    )
    args = ap.parse_args()

    work = args.work_dir
    todo = args.chunks or pending_chunks(work)
    if args.force:
        for cid in todo:
            (work / "translation" / "polished" / f"{cid}.md").unlink(missing_ok=True)
    if args.recheck:
        todo = args.chunks or all_chunks(work)
        broken = []
        for cid in todo:
            if not valid(work, cid):
                (work / "translation" / "polished" / f"{cid}.md").unlink(missing_ok=True)
                broken.append(cid)
        todo = broken
    else:
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
