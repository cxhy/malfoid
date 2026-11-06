#!/usr/bin/env python3
"""Reliable incremental update when the user provides a newer source version.

Usage:
    .venv/bin/python tools/incremental_update.py works/<work> <new source.html|pdf|md>
    .venv/bin/python tools/incremental_update.py works/<work> <new source> --dry-run

Approach: content-addressed rebuild.
  1. Convert the new source into a staging work directory.
  2. Re-chunk it (chapter-aligned, so unchanged chapters produce identical
     chunks regardless of changes elsewhere).
  3. Hash every new source chunk; for each hash that matches an old chunk,
     reuse its translation, polished chunk and proofread report under the new
     chunk id. Unmatched chunks stay pending.
  4. Archive the old source/work_temp/polished/proofread/meta and delivery
     files under <work>/archive/<timestamp>/, then swap the new source and
     temp dir into place.

Nothing is deleted; the archive keeps the previous version for audit. After
the run, translate/polish/proofread only the pending chunks reported at the
end, then merge polished chunks and rebuild volumes.

Dry run converts and chunks into a staging dir but does not swap anything.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = str(ROOT / ".venv/bin/python")
CONVERT = str(ROOT / ".pi/skills/translate-book/scripts/convert.py")


def run(cmd: list[str]) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        sys.exit(f"command failed: {' '.join(cmd)}\n{proc.stdout}\n{proc.stderr}")


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def detect_html(path: Path) -> str:
    text = path.read_text(encoding="utf-8", errors="ignore")[:200000]
    if 'id="chap_1"' in text or "id='chap_1'" in text:
        return "fic"
    if 'id="chapters"' in text or "id='chapters'" in text:
        return "ao3"
    return "ao3"


def stage_source(work: Path, source: Path, staging: Path) -> Path:
    """Convert the new source into a staging work dir and return it."""
    staging.mkdir(parents=True, exist_ok=True)
    old_meta = json.loads((work / "meta.json").read_text(encoding="utf-8"))
    suffix = source.suffix.lower()

    if suffix in (".md", ".markdown"):
        staged = staging / work.name
        (staged / "source").mkdir(parents=True)
        shutil.copyfile(source, staged / "source" / "work.md")
        (staged / "meta.json").write_text(
            json.dumps(old_meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        return staged

    if suffix == ".pdf":
        cmd = [
            PY,
            str(ROOT / "tools/pdf_convert.py"),
            str(source),
            "--root",
            str(staging),
            "--work-id",
            str(old_meta.get("work_id") or "unknown"),
            "--force",
        ]
        run(cmd)
    elif suffix in (".html", ".htm"):
        tool = "fic_convert.py" if detect_html(source) == "fic" else "ao3_convert.py"
        run([PY, str(ROOT / "tools" / tool), str(source), "--root", str(staging), "--force"])
    else:
        sys.exit(f"unsupported source type: {source.suffix}")

    dirs = [p for p in staging.iterdir() if p.is_dir()]
    if len(dirs) != 1:
        sys.exit(f"expected one staged work dir, found {len(dirs)}")
    return dirs[0]


def chunk_staged(staged: Path) -> Path:
    run(
        [
            PY,
            CONVERT,
            str(staged / "source" / "work.md"),
            "--olang",
            "zh",
            "--chunk-size",
            "12000",
            "--temp-root",
            str(staged),
        ]
    )
    temp = staged / "work_temp"
    if not temp.is_dir():
        sys.exit(f"missing staged chunks: {temp}")
    return temp


def old_chunk_map(work: Path) -> dict[str, str]:
    mapping: dict[str, str] = {}
    temp = work / "work_temp"
    for p in sorted(temp.glob("chunk*.md")):
        if p.name.startswith("output_"):
            continue
        mapping.setdefault(sha(p.read_text(encoding="utf-8")), p.stem)
    return mapping


def move(src: Path, dst: Path) -> None:
    if src.exists():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))


def update(work: Path, source: Path, dry_run: bool) -> None:
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    staging = work / "update_staging" / ts
    staged = stage_source(work, source, staging)
    staged_temp = chunk_staged(staged)

    old_map = old_chunk_map(work)
    new_chunks = sorted(
        p for p in staged_temp.glob("chunk*.md") if not p.name.startswith("output_")
    )
    matched: dict[str, str] = {}
    pending: list[str] = []
    for p in new_chunks:
        h = sha(p.read_text(encoding="utf-8"))
        if h in old_map:
            matched[p.stem] = old_map[h]
        else:
            pending.append(p.stem)

    print(f"new chunks: {len(new_chunks)}  matched: {len(matched)}  pending: {len(pending)}")
    if pending:
        print("pending:", ", ".join(pending))

    if dry_run:
        print("dry run: no files swapped")
        return

    archive = work / "archive" / ts
    archive.mkdir(parents=True, exist_ok=True)
    move(work / "source", archive / "source")
    move(work / "work_temp", archive / "work_temp")
    move(work / "meta.json", archive / "meta.json")
    for name in ("polished", "proofread", "draft"):
        move(work / "translation" / name, archive / name)
    for p in sorted((work / "translation").glob("*.*")):
        move(p, archive / "delivery" / p.name)

    move(staged / "source", work / "source")
    move(staged_temp, work / "work_temp")
    move(staged / "meta.json", work / "meta.json")

    # Preserve old metadata fields the new conversion may not know.
    old_meta = json.loads((archive / "meta.json").read_text(encoding="utf-8"))
    new_meta_path = work / "meta.json"
    new_meta = json.loads(new_meta_path.read_text(encoding="utf-8"))
    for key in ("title", "author", "url", "work_id", "source_site"):
        if old_meta.get(key) and not new_meta.get(key):
            new_meta[key] = old_meta[key]
    new_meta_path.write_text(
        json.dumps(new_meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    polished = work / "translation" / "polished"
    proofread = work / "translation" / "proofread"
    polished.mkdir(parents=True, exist_ok=True)
    proofread.mkdir(parents=True, exist_ok=True)

    copied = {"output": 0, "polished": 0, "proofread": 0}
    for new_id, old_id in matched.items():
        src = archive / "work_temp" / f"output_{old_id}.md"
        if src.is_file():
            shutil.copyfile(src, work / "work_temp" / f"output_{new_id}.md")
            copied["output"] += 1
        src = archive / "polished" / f"{old_id}.md"
        if src.is_file():
            shutil.copyfile(src, polished / f"{new_id}.md")
            copied["polished"] += 1
        src = archive / "proofread" / f"{old_id}.md"
        if src.is_file():
            shutil.copyfile(src, proofread / f"{new_id}.md")
            copied["proofread"] += 1

    glossary = archive / "work_temp" / "glossary.json"
    if glossary.is_file():
        shutil.copyfile(glossary, work / "work_temp" / "glossary.json")

    run([PY, str(ROOT / "tools/make_prompt_ctx.py"), str(work / "work_temp")])

    report = [
        "# 增量更新报告",
        "",
        f"- 时间：{ts}",
        f"- 新源文件：`{source}`",
        f"- 新分块：{len(new_chunks)}",
        f"- 复用：{len(matched)}（译文 {copied['output']} / 润色 {copied['polished']} / 校对 {copied['proofread']}）",
        f"- 待翻译：{len(pending)}",
        f"- 旧版本归档：`{archive}`",
        "",
        "## 待处理块",
        "",
        ", ".join(pending) if pending else "无",
        "",
    ]
    (archive / "update-report.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    print(f"archive: {archive}")
    print(f"pending after update: {len(pending)}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("work_dir", type=Path)
    ap.add_argument("source", type=Path)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    update(args.work_dir, args.source, args.dry_run)


if __name__ == "__main__":
    main()
