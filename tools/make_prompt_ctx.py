#!/usr/bin/env python3
"""Build per-chunk prompt context files (glossary table + neighbor excerpts).

Usage:
    .venv/bin/python tools/make_prompt_ctx.py works/<work>/work_temp [chunk0001 ...]

Without chunk arguments, every chunk*.md in the temp dir is processed.
Writes <temp_dir>/prompt_ctx/<chunk>.md, consumed by translate_chunk.py.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / ".pi/skills/translate-book/scripts"
sys.path.insert(0, str(SCRIPTS))

from chunk_context import format_for_prompt, get_neighbor_context  # noqa: E402
from glossary import format_terms_for_prompt, load_glossary, select_terms_for_chunk  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("temp_dir", type=Path)
    ap.add_argument("chunks", nargs="*")
    args = ap.parse_args()

    temp = args.temp_dir
    glossary = load_glossary(str(temp / "glossary.json"))
    out_dir = temp / "prompt_ctx"
    out_dir.mkdir(exist_ok=True)

    chunks = args.chunks or sorted(
        p.name for p in temp.glob("chunk*.md") if not p.name.startswith("output_")
    )
    chunks = [n if n.endswith(".md") else f"{n}.md" for n in chunks]
    for name in chunks:
        text = (temp / name).read_text(encoding="utf-8")
        table = format_terms_for_prompt(select_terms_for_chunk(glossary, text))
        ctx = format_for_prompt(get_neighbor_context(str(temp), name, chars=400))
        parts = []
        if table:
            parts.append("## 术语表（必须逐字采用）\n" + table)
        if ctx:
            parts.append("## 邻居上下文（只读，不翻译、不写入输出）\n" + ctx)
        (out_dir / f"{Path(name).stem}.md").write_text("\n\n".join(parts) + "\n", encoding="utf-8")
        print(f"{name}: {len(table)} chars terms, ctx {len(ctx)} chars")


if __name__ == "__main__":
    main()
