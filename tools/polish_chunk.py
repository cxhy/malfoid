#!/usr/bin/env python3
"""Polish one translated chunk with the local provider (fiction-humanizer-zh rules).

Usage:
    .venv/bin/python tools/polish_chunk.py <work_dir> chunk0001 [--effort medium]

Reads <work_dir>/work_temp/output_<chunk>.md and glossary.json; writes
<work_dir>/translation/polished/<chunk>.md. The original translation is never
overwritten. Prints structural checks (headings, blocks, length ratio, glossary
terms, forbidden names, English residue) after polishing.

Mode: 轻改 (light edit) — fix translationese, dialogue, rhythm and punctuation
only. Plot, characters, foreshadowing, settings and paragraph order stay put.
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
    "你是一位中文小说润色编辑，负责把英文同人小说的译文改成读起来像中文原创小说的文字。"
    "你只输出润色后的正文，不写说明、前言、后记、译注或任何对话式内容。"
)

LEVEL_RULES = {
    "light": """润色要求（轻改档）：
1. 只输出润色后的完整正文，不要任何说明、开头语、结尾语或修改清单。
2. 保留 Markdown 结构：标题行原样保留；段落顺序不变，段落数量允许 ±10% 的微调；斜体、加粗、链接一律保留。
3. 保留全部情节、人物言行、因果、伏笔、设定、叙述视角和语气强度。不增删场景，不扩写，不补写原文没有的心理活动。
4. 专治翻译腔，优先处理这些痕迹：
   - 英文语序直译、从句套从句、一句话里挂三四个“的”；
   - “意识到”“感到”“明白”这类标签化心理动词，改成动作、停顿、身体反应或直接不说；
   - 抽象名词堆叠（“一种……的感觉”“带着几分……的理所当然”），换成具体可感的东西；
   - 被动句、代词泛滥（满篇“他/她/它”）、量词“一个”泛滥；
   - 副词“非常”“十分”“很”堆砌，用更准的动词和名词承担程度。
5. 对白要像人在现场说话：符合人物年龄、身份、关系和当下情绪；对话标签多样化，不要逐句“说道”“问道”；能用动作、停顿、打断表达时，不要写成长段解释。保留原意，脏话粗话照实，不美化。
6. 节奏：长短句结合，紧张处句子短，盘算和过渡处可以长；避免连续多段都是同一长度和同一结构。
7. 标点按中文习惯：对话用“”，省略号用……，破折号用——，不用英文标点；句末标点放在引号内。
8. 术语表里的专有名词逐字使用指定译名；Darcy 单独出现译“德拉科妮娅”，Dar 译“德拉”，全名译“德拉科妮娅·马尔福”；禁止出现“达西”。
9. 不添加译者注，不解释文化背景，不保留英文原词。
10. 篇幅与原文相当：中文总字数控制在原文的 80%–115%，既不扩写成赏析，也不压成梗概。""",
    "refine": """润色要求（精修档）：
1. 只输出润色后的完整正文，不要任何说明、开头语、结尾语或修改清单。
2. 保留 Markdown 结构：标题行原样保留；段落顺序可以微调，段落数量允许 ±30% 的增减（可合并或拆分段落，但不改变场景顺序）；斜体、加粗、链接一律保留。
3. 保留全部情节、人物言行、因果关系、伏笔、设定、叙述视角和关键信息点。可以补写原文已经暗示的动作、感官细节、情绪递进和场景余波，但不新增原文没有的事件、人物、设定或结果。
4. 在轻改档的基础上进一步去 AI 味，重点处理：
   - 剧情像大纲、人物只解释不行动、情绪只有标签；
   - 对白互相汇报信息、冲突没有代价、场景转折只有结果没有过程；
   - 抽象名词堆叠、被动句、代词泛滥、副词堆砌；
   - 环境描写脱离人物，结尾只预告不制造悬念。
5. 对白要像人在现场说话：符合人物年龄、身份、关系和当下情绪；对话标签多样化，能用动作、停顿、打断表达时，不要写成长段解释。保留原意，脏话粗话照实，不美化。
6. 节奏：长短句结合，紧张处句子短，盘算和过渡处可以长；每 300–500 字给一个自然的小转折、反问、压力升级或情绪落点。
7. 标点按中文习惯：对话用“”，省略号用……，破折号用——，不用英文标点；句末标点放在引号内。
8. 术语表里的专有名词逐字使用指定译名；Darcy 单独出现译“德拉科妮娅”，Dar 译“德拉”，全名译“德拉科妮娅·马尔福”；禁止出现“达西”。
9. 不添加译者注，不解释文化背景，不保留英文原词。
10. 篇幅：中文总字数控制在原文的 85%–125%；允许为补足场景动作和余波适度扩写，但不得把场景写成梗概或赏析。""",
    "rewrite": """润色要求（重写档）：
1. 只输出润色后的完整正文，不要任何说明、开头语、结尾语或修改清单。
2. 保留 Markdown 标题行；段落顺序和数量可以较大调整（±40%），可以重构场景链、合并或拆分场景片段，但章节标题、场景顺序的基本逻辑不变；斜体、加粗、链接一律保留。
3. 保留人物、设定、章节功能、核心事件、因果链和结局；可以重写场景推进方式、补足铺垫/过程/余波、调整冲突节奏，但不得改变事件结果、人物关系走向、伏笔和关键信息点。
4. 最强力度的去 AI 味：把剧情说明改成场景，把情绪标签改成动作和选择，把信息对白改成有目的的现场对话；允许改变叙述的切入点和段落节奏，但不得新增原文没有的事件、人物或设定。
5. 对白要像人在现场说话：符合人物年龄、身份、关系和当下情绪；对话标签多样化，能用动作、停顿、打断表达时，不要写成长段解释。保留原意，脏话粗话照实，不美化。
6. 节奏：长短句结合，紧张处句子短，盘算和过渡处可以长；每个场景要有铺垫、过程、余波，冲突要有代价。
7. 标点按中文习惯：对话用“”，省略号用……，破折号用——，不用英文标点；句末标点放在引号内。
8. 术语表里的专有名词逐字使用指定译名；Darcy 单独出现译“德拉科妮娅”，Dar 译“德拉”，全名译“德拉科妮娅·马尔福”；禁止出现“达西”。
9. 不添加译者注，不解释文化背景，不保留英文原词。
10. 篇幅：中文总字数控制在原文的 90%–135%；允许重构和扩写，但不得把场景写成梗概、赏析或另一条情节线。""",
}

# Per-level structural tolerances for check().
LEVEL_TOLERANCE = {
    "light": (0.15, 0.80, 1.15),
    "refine": (0.30, 0.85, 1.25),
    "rewrite": (0.40, 0.90, 1.35),
}


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
        "temperature": 0.5,
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
    text = re.sub(r"^(润色后(的)?(正文|版本)?|润色稿|译文)[:：]\s*\n?", "", text)
    return text.strip() + "\n"


CJK = re.compile(r"[\u4e00-\u9fff]")
URLISH = re.compile(r"(!?\[[^\]]*\]\([^)]*\)|<[^>]+>|https?://\S+)")
# Family mottos stay in the original Latin/French with a Chinese gloss after them.
FOREIGN_MOTTO = re.compile(r"(Toujours Pur|Radices Perdurant|Sanctimonia Vinc\w* Semper)")


def blocks(text: str) -> int:
    return len([b for b in re.split(r"\n\s*\n", text) if b.strip()])


def headings(text: str) -> int:
    return len(re.findall(r"(?m)^#{1,6} ", text))


def english_residue(text: str) -> list[str]:
    cleaned = URLISH.sub("", text)
    cleaned = FOREIGN_MOTTO.sub("", cleaned)
    cleaned = re.sub(r"(?m)^#{1,6} .*$", "", cleaned)
    hits = []
    for line in cleaned.split("\n"):
        if re.findall(r"[A-Za-z][A-Za-z'’-]{2,}", line):
            hits.append(line.strip()[:160])
    return hits


def check(old: str, new: str, terms: list[dict], level: str = "light") -> list[str]:
    problems = []
    block_tol, lo, hi = LEVEL_TOLERANCE.get(level, LEVEL_TOLERANCE["light"])
    if headings(old) != headings(new):
        problems.append(f"headings {headings(old)} -> {headings(new)}")
    bo, bn = blocks(old), blocks(new)
    if abs(bo - bn) > max(1, bo * block_tol):
        problems.append(f"blocks {bo} -> {bn}")
    co, cn = len(CJK.findall(old)), len(CJK.findall(new))
    ratio = cn / max(1, co)
    if not (lo <= ratio <= hi):
        problems.append(f"length {co} -> {cn} ({ratio:.2f})")
    if "达西" in new:
        problems.append("forbidden name 达西")
    residue = english_residue(new)
    if residue:
        problems.append(f"english residue: {residue[0]}")
    missing = [t["target"] for t in terms if t["target"] in old and t["target"] not in new]
    if missing:
        problems.append(f"missing terms: {', '.join(missing)}")
    return problems


def split_blocks(text: str, n: int) -> list[str]:
    """Split at paragraph boundaries into n roughly equal parts."""
    blocks = [b for b in re.split(r"\n\s*\n", text) if b.strip()]
    total = sum(len(b) for b in blocks)
    parts: list[str] = []
    cur: list[str] = []
    acc = 0
    targets = [total * (i + 1) / n for i in range(n)]
    ti = 0
    for b in blocks:
        cur.append(b)
        acc += len(b)
        if ti < n - 1 and acc >= targets[ti]:
            parts.append("\n\n".join(cur))
            cur = []
            ti += 1
    if cur:
        parts.append("\n\n".join(cur))
    return parts


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("work_dir", type=Path)
    ap.add_argument("chunk")
    ap.add_argument("--effort", default="minimal")
    ap.add_argument(
        "--model",
        default=os.environ.get("MALFOID_MODEL", "DeepSeek-V4.1-Flash-1017-TensorFold-Zig"),
    )
    ap.add_argument("--max-tokens", type=int, default=16000)
    ap.add_argument(
        "--split",
        type=int,
        default=1,
        help="polish the chunk in N parts and concatenate (for long chunks where reasoning overflows)",
    )
    ap.add_argument(
        "--level",
        choices=["light", "refine", "rewrite"],
        default="light",
        help="polish intensity; default light",
    )
    ap.add_argument(
        "--out",
        type=Path,
        default=None,
        help="write the result here instead of translation/polished/<chunk>.md",
    )
    args = ap.parse_args()

    work = args.work_dir
    temp = work / "work_temp"
    src = (temp / f"output_{args.chunk}.md").read_text(encoding="utf-8")
    glossary = json.loads((temp / "glossary.json").read_text(encoding="utf-8"))
    terms = glossary["terms"]

    term_lines = "\n".join(
        f"- {t['source']} → {t['target']}"
        + (f"（别名：{', '.join(t['aliases'])}）" if t.get("aliases") else "")
        + (f"；{t['notes']}" if t.get("notes") else "")
        for t in terms
    )

    rules = LEVEL_RULES[args.level]
    parts = split_blocks(src, args.split) if args.split > 1 else [src]
    base_url, api_key = load_config()
    outs = []
    usages = []
    for i, part in enumerate(parts, 1):
        prompt = (
            f"{rules}\n\n"
            f"术语表（润色时保持这些译名不变）：\n{term_lines}\n\n"
            f"待润色译文（第 {i}/{len(parts)} 段）：\n\n{part}"
        )
        out, usage = call(base_url, api_key, args.model, prompt, args.effort, args.max_tokens)
        outs.append(clean(out))
        usages.append(usage)

    out = "\n\n".join(o.strip() for o in outs).strip() + "\n"
    dest = args.out or (work / "translation" / "polished" / f"{args.chunk}.md")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(out, encoding="utf-8")

    problems = check(src, out, terms, args.level)
    print(f"{args.chunk}: -> {dest} level={args.level} split={len(parts)} usage {usages}")
    print("  OK" if not problems else "  WARN " + "; ".join(problems))
    if problems:
        sys.exit(1)


if __name__ == "__main__":
    main()
