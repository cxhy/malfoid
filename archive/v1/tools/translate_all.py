#!/usr/bin/env python3
"""Translate every pending chunk of a work, repair residue, then QA.

Usage:
    .venv/bin/python tools/translate_all.py works/<work>/work_temp [chunk0001 ...]

The local provider serializes requests, so the default is one worker. After the
first pass, chunks with untranslated English words go through fix_residue.py;
chunks failing structurally are retried once at medium effort.
"""

from __future__ import annotations

import argparse
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = str(ROOT / ".venv/bin/python")


def pending_chunks(temp: Path) -> list[str]:
    out = []
    for p in sorted(temp.glob("chunk*.md")):
        if p.name.startswith("output_"):
            continue
        if not (temp / f"output_{p.stem}.md").is_file():
            out.append(p.stem)
    return out


def translate(temp: Path, cid: str, effort: str) -> tuple[str, bool, str]:
    proc = subprocess.run(
        [PY, str(ROOT / "tools/translate_chunk.py"), str(temp), cid, "--effort", effort],
        capture_output=True,
        text=True,
    )
    ok = proc.returncode == 0 and (temp / f"output_{cid}.md").is_file()
    tail = (proc.stdout + proc.stderr).strip().splitlines()[-1:] or [""]
    return cid, ok, tail[0]


def qa(temp: Path, cid: str) -> tuple[bool, str]:
    proc = subprocess.run(
        [PY, str(ROOT / "tools/qa_translation.py"), str(temp), cid],
        capture_output=True,
        text=True,
    )
    return proc.returncode == 0, (proc.stdout + proc.stderr).strip()


def residue_only(msg: str) -> bool:
    return "english residue" in msg and "FAIL headings" not in msg and "FAIL length" not in msg


def run_batch(temp: Path, chunks: list[str], workers: int, effort: str) -> list[str]:
    failures = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(translate, temp, cid, effort): cid for cid in chunks}
        for n, fut in enumerate(as_completed(futures), 1):
            cid, ok, msg = fut.result()
            print(f"[{n}/{len(chunks)}] {cid}: {'ok' if ok else 'FAIL'} {msg}", flush=True)
            if not ok:
                failures.append(cid)
    return failures


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("temp_dir", type=Path)
    ap.add_argument("chunks", nargs="*")
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--effort", default="low")
    ap.add_argument("--retries", type=int, default=1)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    temp = args.temp_dir
    todo = args.chunks or pending_chunks(temp)
    if args.force:
        for cid in todo:
            (temp / f"output_{cid}.md").unlink(missing_ok=True)
    todo = [c for c in todo if not (temp / f"output_{c}.md").exists()]
    if not todo:
        print("nothing to translate")
        return

    missing = [c for c in todo if not (temp / "prompt_ctx" / f"{c}.md").is_file()]
    if missing:
        subprocess.run(
            [PY, str(ROOT / "tools/make_prompt_ctx.py"), str(temp), *missing], check=True
        )

    print(f"translating {len(todo)} chunks with {args.workers} worker(s)")
    hard = run_batch(temp, todo, args.workers, args.effort)

    # Repair untranslated English words without redoing the whole chunk.
    residue = []
    for cid in todo:
        if cid in hard:
            continue
        ok, msg = qa(temp, cid)
        if not ok and residue_only(msg):
            residue.append(cid)
        elif not ok:
            hard.append(cid)
            print(f"qa {cid} structural failure:\n{msg}", flush=True)
    if residue:
        print(f"repairing residue in {len(residue)} chunk(s)")
        subprocess.run([PY, str(ROOT / "tools/fix_residue.py"), str(temp), *residue], check=False)

    # Structural retries.
    for attempt in range(1, args.retries + 1):
        retry = []
        for cid in todo:
            if cid in hard:
                continue
            ok, msg = qa(temp, cid)
            if not ok:
                retry.append(cid)
                print(f"qa {cid} still failing (attempt {attempt}):\n{msg}", flush=True)
        if not retry:
            break
        print(f"retranslating {len(retry)} chunk(s) (attempt {attempt})")
        hard.extend(run_batch(temp, retry, args.workers, args.effort))

    bad = sorted(set(hard))
    print("done" if not bad else f"failed: {', '.join(bad)}")


if __name__ == "__main__":
    main()
