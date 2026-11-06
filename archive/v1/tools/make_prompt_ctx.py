#!/usr/bin/env python3
"""Build per-chunk prompt context files.

Usage:
    .venv/bin/python tools/make_prompt_ctx.py works/<work>/work_temp [chunk0001 ...]

Without chunk arguments, every chunk*.md in the temp dir is processed.
Writes <temp_dir>/prompt_ctx/<chunk>.md, consumed by translate_chunk.py.

Context order follows the Wenyi-style prescan:
    whole-book style brief -> HP story bible -> book synopsis -> chapter digest
    -> glossary -> recent translated context -> next source lookahead -> neighbor
    source.

If work_temp/prescan/ is absent, the tool falls back to the older
glossary + neighbor behavior.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / ".pi/skills/translate-book/scripts"
sys.path.insert(0, str(SCRIPTS))

from chunk_context import format_for_prompt, get_neighbor_context  # noqa: E402
from glossary import format_terms_for_prompt, load_glossary, select_terms_for_chunk  # noqa: E402

ROLLING_CHARS = 1200
LOOKAHEAD_CHARS = 500


def read_optional(path: Path) -> str:
    return path.read_text(encoding="utf-8").strip() if path.is_file() else ""


def natural(name: str) -> int:
    m = re.search(r"(\d+)$", name)
    return int(m.group(1)) if m else 0


def translated_text(temp: Path, work: Path, chunk: str) -> str:
    """Prefer the polished chunk; fall back to the raw translation."""
    polished = work / "translation" / "polished" / f"{chunk}.md"
    if polished.is_file():
        return polished.read_text(encoding="utf-8").strip()
    raw = temp / f"output_{chunk}.md"
    if raw.is_file():
        return raw.read_text(encoding="utf-8").strip()
    return ""


def rolling_context(temp: Path, work: Path, previous: list[str]) -> str:
    """Recent translated paragraphs, newest last, bounded by ROLLING_CHARS."""
    buf = ""
    for chunk in reversed(previous):
        text = translated_text(temp, work, chunk)
        if not text:
            continue
        paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
        picked: list[str] = []
        size = 0
        for para in reversed(paras):
            if size + len(para) > ROLLING_CHARS:
                break
            picked.append(para)
            size += len(para)
        if picked:
            buf = "\n\n".join(reversed(picked)) + ("\n\n" + buf if buf else "")
        if len(buf) >= ROLLING_CHARS:
            break
    return buf.strip()[-ROLLING_CHARS:] if buf else ""


def lookahead(temp: Path, next_chunk: str | None) -> str:
    if not next_chunk:
        return ""
    text = (temp / f"{next_chunk}.md").read_text(encoding="utf-8")
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    if not paras:
        return ""
    first = paras[0]
    if len(first) > LOOKAHEAD_CHARS:
        first = first[:LOOKAHEAD_CHARS]
    return first


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("temp_dir", type=Path)
    ap.add_argument("chunks", nargs="*")
    args = ap.parse_args()

    temp = args.temp_dir
    work = temp.parent
    glossary = load_glossary(str(temp / "glossary.json"))
    out_dir = temp / "prompt_ctx"
    out_dir.mkdir(exist_ok=True)

    prescan = temp / "prescan"
    style_brief = read_optional(prescan / "style_brief.md")
    story_bible = read_optional(prescan / "story_bible.md")
    book_synopsis = read_optional(prescan / "book_synopsis.md")
    chunk_map: dict[str, int] = {}
    map_path = prescan / "chunk_map.json"
    if map_path.is_file():
        chunk_map = json.loads(map_path.read_text(encoding="utf-8"))

    all_chunks = sorted(
        (p.stem for p in temp.glob("chunk*.md") if not p.name.startswith("output_")),
        key=natural,
    )
    chunks = args.chunks or all_chunks
    chunks = [n if n.endswith(".md") else f"{n}.md" for n in chunks]
    for name in chunks:
        stem = Path(name).stem
        text = (temp / name).read_text(encoding="utf-8")
        parts: list[str] = []

        if style_brief:
            parts.append("## 全书风格 brief（只读，保持叙述声音）\n" + style_brief)
        if story_bible:
            parts.append(
                "## 哈利波特同人 story bible（只读，保持人物、时间线、秘密和 AU 设定；"
                "不要写入译文，不要提前揭示）\n" + story_bible
            )
        if book_synopsis:
            parts.append("## 全书 synopsis（只读，翻译用，不要写入译文）\n" + book_synopsis)

        chapter = chunk_map.get(stem)
        if chapter is not None:
            digest = read_optional(prescan / "chapter_digests" / f"ch-{chapter:02d}.md")
            if digest:
                parts.append(f"## 本章 digest（第 {chapter} 章，只读）\n" + digest)

        table = format_terms_for_prompt(select_terms_for_chunk(glossary, text))
        if table:
            parts.append("## 术语表（必须逐字采用）\n" + table)

        idx = all_chunks.index(stem) if stem in all_chunks else -1
        previous = all_chunks[:idx] if idx > 0 else []
        rolling = rolling_context(temp, work, previous)
        if rolling:
            parts.append(
                "## 最近已译上下文（只读，保持代词、称呼和语气；不要重译或写入输出）\n" + rolling
            )

        next_chunk = all_chunks[idx + 1] if 0 <= idx < len(all_chunks) - 1 else None
        ahead = lookahead(temp, next_chunk)
        if ahead:
            parts.append("## 下一块开头（只读，不要提前翻译）\n" + ahead)

        ctx = format_for_prompt(get_neighbor_context(str(temp), name, chars=250))
        if ctx:
            parts.append("## 邻居原文片段（只读，不翻译、不写入输出）\n" + ctx)

        (out_dir / f"{stem}.md").write_text("\n\n".join(parts) + "\n", encoding="utf-8")
        print(
            f"{name}: terms {len(table)}, rolling {len(rolling)}, "
            f"lookahead {len(ahead)}, prescan {'on' if style_brief else 'off'}"
        )


if __name__ == "__main__":
    main()
