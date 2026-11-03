#!/usr/bin/env python3
"""Independent fidelity proofreading for one translated chunk.

Unlike polish_chunk.py (which improves Chinese prose), this pass compares the
English source against the polished Chinese translation and reports only
fidelity problems: omissions, additions, mistranslations, wrong pronouns,
term drift, and source typos rendered literally. It never rewrites the chunk;
it writes a report to <work>/translation/proofread/<chunk>.md so a human (or
the Master) can apply minimal fixes.

Usage:
    .venv/bin/python tools/proofread_chunk.py <work_dir> chunk0001
    .venv/bin/python tools/proofread_chunk.py <work_dir> chunk0001 --effort medium
    .venv/bin/python tools/proofread_chunk.py <work_dir> chunk0001 --source draft --force

The translation compared against the source defaults to the polished chunk
(translation/polished/<chunk>.md) and falls back to the raw translation
(work_temp/output_<chunk>.md) when no polished chunk exists yet.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

MODELS = Path.home() / ".pi/agent/models.json"
PROVIDER = "deepseek-v4-1-flash"

SYSTEM = (
    "你是一位独立校对编辑，负责对照英文原文检查中文译文的忠实度。"
    "你不改写全文，只找问题并给出最小修改建议。"
)

RULES = """校对要求：
1. 逐段对照英文原文与中文译文，只检查忠实度：漏译（整句、从句或信息点丢失）、增译（原文没有的情节、解释或心理活动）、误译（词义、指代、否定、因果、时态、数量）、人称/性别/指代错误、术语不一致、语气强度偏差、标点导致的语义变化。
2. 原文本身明显的拼写错误或笔误（例如 greasiest 应为 greatest、Eaters 误作 Easters），要按上下文判断，在问题里注明“原文疑似笔误”，并给出符合上下文的译法；不要照字面硬译。
3. 不评价文笔、节奏和润色质量——那是润色环节的职责。只报告忠实度问题。
4. 每条问题必须能定位：给出中文译文中的短句（不超过 30 字）、对应的英文原文短句、问题类型、最小修改建议（只改必要的词或短语，不要整段重写）。
5. 没有问题就写“未发现忠实度问题”，不要为了凑数编造问题。
6. 输出 Markdown 报告，结构固定：
   ## 严重（影响情节、人物、因果）
   ## 一般（信息点偏差、术语、语气强度）
   ## 轻微（标点、指代模糊、可读性）
   每条一行，格式：
   - 【类型】译文：“……”｜原文：“……”｜建议：……
   最后一行只写 `结论：PASS` 或 `结论：FAIL`。
7. 不要输出译文全文，不要输出修改后的整段，不要写前言后记。"""


def load_config() -> tuple[str, str]:
    """Resolve the OpenAI-compatible endpoint.

    Environment variables (MALFOID_BASE_URL / MALFOID_API_KEY) take precedence so
    the tools can be pointed at any provider; otherwise fall back to the local
    pi model registry.
    """
    base = os.environ.get("MALFOID_BASE_URL")
    if base:
        return base.rstrip("/"), os.environ.get("MALFOID_API_KEY", "")
    cfg = json.loads(MODELS.read_text(encoding="utf-8"))["providers"][PROVIDER]
    return cfg["baseUrl"].rstrip("/"), cfg["apiKey"]


def call(
    base_url: str, api_key: str, model: str, prompt: str, effort: str, max_tokens: int
) -> tuple[str, dict]:
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
        "max_tokens": max_tokens,
        "reasoning_effort": effort,
    }
    req = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
    )
    with urllib.request.urlopen(req, timeout=900) as resp:
        data = json.loads(resp.read())
    return data["choices"][0]["message"]["content"], data.get("usage", {})


def clean(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```(?:markdown|md)?\s*\n", "", text)
    text = re.sub(r"\n```\s*$", "", text)
    text = re.sub(r"^(校对报告|校对结果)[:：]\s*\n?", "", text)
    return text.strip() + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("work_dir", type=Path)
    ap.add_argument("chunk")
    ap.add_argument("--effort", default="low")
    ap.add_argument(
        "--model",
        default=os.environ.get("MALFOID_MODEL", "DeepSeek-V4.1-Flash-1017-TensorFold-Zig"),
    )
    ap.add_argument("--max-tokens", type=int, default=32768)
    ap.add_argument(
        "--source",
        choices=["auto", "polished", "draft"],
        default="auto",
        help="which Chinese text to proofread; auto prefers polished, falls back to draft",
    )
    ap.add_argument(
        "--translation",
        type=Path,
        default=None,
        help="proofread this Chinese file instead of the standard polished/draft chunk",
    )
    ap.add_argument(
        "--report",
        type=Path,
        default=None,
        help="write the report here instead of translation/proofread/<chunk>.md",
    )
    ap.add_argument("--force", action="store_true", help="overwrite an existing report")
    args = ap.parse_args()

    work = args.work_dir
    temp = work / "work_temp"
    src = (temp / f"{args.chunk}.md").read_text(encoding="utf-8")
    dest = args.report or (work / "translation" / "proofread" / f"{args.chunk}.md")
    if dest.is_file() and not args.force:
        print(f"{args.chunk}: report exists, skipped ({dest})")
        return

    polished_path = work / "translation" / "polished" / f"{args.chunk}.md"
    draft_path = temp / f"output_{args.chunk}.md"
    if args.translation:
        chosen = args.translation
    elif args.source == "polished":
        chosen = polished_path
    elif args.source == "draft":
        chosen = draft_path
    else:
        chosen = polished_path if polished_path.is_file() else draft_path
    if not chosen.is_file():
        sys.exit(f"missing translation chunk: {chosen}")
    polished = chosen.read_text(encoding="utf-8")
    label = (
        "中文润色稿"
        if chosen == polished_path
        else ("中文初译稿" if chosen == draft_path else "待校对译文")
    )
    glossary = json.loads((temp / "glossary.json").read_text(encoding="utf-8"))

    term_lines = "\n".join(
        f"- {t['source']} → {t['target']}"
        + (f"（别名：{', '.join(t['aliases'])}）" if t.get("aliases") else "")
        + (f"；{t['notes']}" if t.get("notes") else "")
        for t in glossary["terms"]
    )

    prompt = (
        f"{RULES}\n\n"
        f"术语表（用于核对译名一致性）：\n{term_lines}\n\n"
        f"英文原文：\n\n{src}\n\n"
        f"{label}：\n\n{polished}"
    )

    base_url, api_key = load_config()
    out, usage = call(base_url, api_key, args.model, prompt, args.effort, args.max_tokens)
    out = clean(out)

    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(out, encoding="utf-8")

    severe = len(re.findall(r"(?m)^- 【[^】]*】", out.split("## 一般")[0]))
    total = len(re.findall(r"(?m)^- 【[^】]*】", out))
    verdict = "PASS" if "结论：PASS" in out else ("FAIL" if "结论：FAIL" in out else "UNKNOWN")
    print(f"{args.chunk}: {verdict} issues={total} severe={severe} -> {dest} usage {usage}")
    if verdict == "UNKNOWN":
        sys.exit(1)


if __name__ == "__main__":
    main()
