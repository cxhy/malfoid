#!/usr/bin/env python3
"""Apply minimal fidelity fixes to a polished chunk, guided by a proofread report.

Independent context: gets the English source, the polished Chinese chunk, the
proofread report and the glossary; edits only the phrases the report flags as
real problems (skipping items the report itself calls acceptable), and returns
the full chunk with structure unchanged.

Usage:
    .venv/bin/python tools/fix_proofread.py <work_dir> chunk0001
    .venv/bin/python tools/fix_proofread.py <work_dir> chunk0001 --dry-run

Writes translation/polished/<chunk>.md only after a structural check against
work_temp/output_<chunk>.md passes; on failure it keeps the old file.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from polish_chunk import check  # noqa: E402
from proofread_chunk import call, clean, load_config  # noqa: E402

SYSTEM = (
    "你是一位校对修复编辑。你只按校对报告做最小改动，把中文译文改到忠实于英文原文；"
    "你不重写文笔，不调整段落顺序，不新增原文没有的内容。"
)

RULES = """修复要求：
1. 只处理校对报告里明确认定为问题的条目；报告里写着“可保留”“无需改”“无误”“正确”的条目一律不动。
2. 每条问题只做报告建议的最小改动（改几个词或一个短语），不要整段重写，不要顺带润色其他句子。
3. 保留原有的 Markdown 结构：标题行、段落数量、段落顺序、空行位置完全不变。
4. 保留原有的中文标点和对话引号风格；不要新增或删除段落。
5. 人名、地名、咒语按术语表；原文明显笔误按上下文译，不要照字面硬译。
6. 输出完整的中文润色稿全文，不要输出说明、前言、注释或代码块围栏。"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("work_dir", type=Path)
    ap.add_argument("chunk")
    ap.add_argument("--effort", default="none")
    ap.add_argument(
        "--model",
        default=os.environ.get("MALFOID_MODEL", "DeepSeek-V4.1-Flash-1017-TensorFold-Zig"),
    )
    ap.add_argument("--max-tokens", type=int, default=32768)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    work = args.work_dir
    temp = work / "work_temp"
    src = (temp / f"{args.chunk}.md").read_text(encoding="utf-8")
    polished_path = work / "translation" / "polished" / f"{args.chunk}.md"
    report_path = work / "translation" / "proofread" / f"{args.chunk}.md"
    if not polished_path.is_file():
        sys.exit(f"missing polished chunk: {polished_path}")
    if not report_path.is_file():
        sys.exit(f"missing proofread report: {report_path}")
    polished = polished_path.read_text(encoding="utf-8")
    report = report_path.read_text(encoding="utf-8")

    glossary = json.loads((temp / "glossary.json").read_text(encoding="utf-8"))
    term_lines = "\n".join(
        f"- {t['source']} → {t['target']}"
        + (f"（别名：{', '.join(t['aliases'])}）" if t.get("aliases") else "")
        for t in glossary["terms"]
    )

    prompt = (
        f"{RULES}\n\n术语表：\n{term_lines}\n\n"
        f"英文原文：\n\n{src}\n\n"
        f"校对报告：\n\n{report}\n\n"
        f"中文润色稿（在其基础上做最小修改，输出修改后的全文）：\n\n{polished}"
    )

    base_url, api_key = load_config()
    out, usage = call(base_url, api_key, args.model, prompt, args.effort, args.max_tokens)
    out = clean(out)
    print(f"{args.chunk}: usage {usage}", flush=True)
    if args.dry_run:
        Path(f"/tmp/{args.chunk}.fixed.md").write_text(out, encoding="utf-8")
        print(f"dry run -> /tmp/{args.chunk}.fixed.md")
        return

    if out.strip() == polished.strip():
        print(f"{args.chunk}: no change")
        return

    old_terms = json.loads((temp / "glossary.json").read_text(encoding="utf-8"))["terms"]
    problems = check((temp / f"output_{args.chunk}.md").read_text(encoding="utf-8"), out, old_terms)
    if problems:
        Path(f"/tmp/{args.chunk}.fixed.rejected.md").write_text(out, encoding="utf-8")
        print(f"{args.chunk}: REJECTED, old file kept; problems: {problems}")
        print(f"candidate saved to /tmp/{args.chunk}.fixed.rejected.md")
        sys.exit(1)

    polished_path.write_text(out, encoding="utf-8")
    print(f"{args.chunk}: fixed -> {polished_path}")


if __name__ == "__main__":
    main()
