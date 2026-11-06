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
    .venv/bin/python tools/proofread_chunk.py <work_dir> chunk0001 --effort low
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

import prompt_profile
import term_filter

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
5. 只报告确定的、需要改动的错误；不确定的、译文可接受的不报。不要为了凑数编造问题。
6. 不要报告“此项不成立”“无需修改”“原文疑似笔误但译文正确”这类条目；原文笔误只在译文确实需要改时才写进问题。
7. 同一问题只报一次；不要在不同严重级别重复同一条。每条必须给出具体的最小修改建议；如果建议是“无需修改”，就不要列这条。
8. 不评价文笔、节奏、润色质量；不要提“建议润色”类意见。
9. 严重 = 影响情节、人物、因果、否定、时态；一般 = 信息点、术语、语气强度；轻微 = 标点、指代模糊、可读性。
10. 输出 Markdown 报告，结构固定：
   ## 严重（影响情节、人物、因果）
   ## 一般（信息点偏差、术语、语气强度）
   ## 轻微（标点、指代模糊、可读性）
   每条一行，格式：
   - 【类型】译文：“……”｜原文：“……”｜建议：……
   最后一行只写 `结论：PASS` 或 `结论：FAIL`。
11. 不要输出译文全文，不要输出修改后的整段，不要写前言后记。"""


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
    choice = data["choices"][0]
    usage = data.get("usage", {})
    usage["finish_reason"] = choice.get("finish_reason")
    return choice["message"]["content"], usage


def reasoning_overflow(usage: dict, max_tokens: int) -> bool:
    details = usage.get("completion_tokens_details") or {}
    reasoning = details.get("reasoning_tokens") or 0
    completion = usage.get("completion_tokens") or 0
    return (
        usage.get("finish_reason") == "length"
        or reasoning >= max_tokens * 0.95
        or completion >= max_tokens
    )


def clean(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```(?:markdown|md)?\s*\n", "", text)
    text = re.sub(r"\n```\s*$", "", text)
    text = re.sub(r"^(校对报告|校对结果)[:：]\s*\n?", "", text)
    return text.strip() + "\n"


def split_paragraphs(text: str, n: int) -> list[str]:
    """Split text into n contiguous parts with equal paragraph counts.

    Light polishing preserves paragraph count, so source and translation split
    at the same paragraph indices; this keeps each part aligned.
    """
    paras = [p.strip() for p in re.split(r"\n\s*\n", text.strip()) if p.strip()]
    if n <= 1 or len(paras) <= 1:
        return [text.strip()]
    n = min(n, len(paras))
    bounds = [round(k * len(paras) / n) for k in range(n + 1)]
    bounds[0] = 0
    bounds[-1] = len(paras)
    parts = []
    for k in range(n):
        parts.append("\n\n".join(paras[bounds[k] : bounds[k + 1]]))
    return parts


SECTION_RE = re.compile(r"(?m)^##\s*(严重|一般|轻微)")


def parse_report(text: str) -> tuple[dict[str, list[str]], str]:
    sections: dict[str, list[str]] = {}
    matches = list(SECTION_RE.finditer(text))
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        after = text[m.end() : end]
        # drop the rest of the "## 严重（...）" header line itself
        after = after.split("\n", 1)[1] if "\n" in after else ""
        body = re.split(r"(?m)^结论[:：]", after)[0]
        lines = []
        for line in body.splitlines():
            line = line.strip()
            if not line or line in ("无", "（无）") or line.startswith("未发现"):
                continue
            lines.append(line)
        sections[m.group(1)] = lines
    verdict = "FAIL" if "结论：FAIL" in text else ("PASS" if "结论：PASS" in text else "UNKNOWN")
    return sections, verdict


def merge_reports(reports: list[str]) -> str:
    merged = {"严重": [], "一般": [], "轻微": []}
    verdicts = []
    for text in reports:
        sections, verdict = parse_report(text)
        verdicts.append(verdict)
        for key in merged:
            merged[key].extend(sections.get(key, []))
    if "UNKNOWN" in verdicts:
        verdict = "UNKNOWN"
    elif "FAIL" in verdicts:
        verdict = "FAIL"
    else:
        verdict = "PASS"
    out = []
    for key, title in (
        ("严重", "## 严重（影响情节、人物、因果）"),
        ("一般", "## 一般（信息点偏差、术语、语气强度）"),
        ("轻微", "## 轻微（标点、指代模糊、可读性）"),
    ):
        out.append(title)
        out.append("\n".join(merged[key]) if merged[key] else "无")
        out.append("")
    out.append(f"结论：{verdict}")
    return "\n".join(out) + "\n"


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
    ap.add_argument(
        "--split",
        type=int,
        default=1,
        help="split the chunk into N parts and proofread each separately (for reasoning overflows)",
    )
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
    rules = prompt_profile.compose(RULES, "proofread", work_dir=work)

    term_lines = term_filter.format_terms(
        term_filter.select_terms(glossary["terms"], src, check_target=True)
    )

    base_url, api_key = load_config()

    def run_split(n: int, allow_none: bool = False):
        def call_part(prompt: str, zh_part: str):
            part_out, part_usage = call(
                base_url, api_key, args.model, prompt, args.effort, args.max_tokens
            )
            part_out = clean(part_out)
            overflow = reasoning_overflow(part_usage, args.max_tokens)
            if "结论" in part_out and not overflow:
                return part_out, part_usage
            # Quality first: split again for large parts; only drop reasoning
            # for small parts or when no further split is available.
            if allow_none:
                retry_out, retry_usage = call(
                    base_url, api_key, args.model, prompt, "none", args.max_tokens
                )
                retry_out = clean(retry_out)
                part_usage = {"primary": part_usage, "fallback": retry_usage}
                if "结论" in retry_out and not reasoning_overflow(retry_usage, args.max_tokens):
                    return retry_out, part_usage
            return None, part_usage

        if n > 1:
            src_parts = split_paragraphs(src, n)
            zh_parts = split_paragraphs(polished, n)
            reports = []
            usage = []
            for s_part, zh_part in zip(src_parts, zh_parts):
                prompt = (
                    f"{rules}\n\n"
                    f"术语表（用于核对译名一致性）：\n{term_lines}\n\n"
                    f"英文原文：\n\n{s_part}\n\n"
                    f"{label}：\n\n{zh_part}"
                )
                part_out, part_usage = call_part(prompt, zh_part)
                if part_out is None:
                    return None, usage + [part_usage]
                reports.append(part_out)
                usage.append(part_usage)
            return merge_reports(reports), usage

        prompt = (
            f"{rules}\n\n"
            f"术语表（用于核对译名一致性）：\n{term_lines}\n\n"
            f"英文原文：\n\n{src}\n\n"
            f"{label}：\n\n{polished}"
        )
        out, usage = call_part(prompt, polished)
        return out, [usage]

    paragraph_count = len([p for p in re.split(r"\n\s*\n", src.strip()) if p.strip()])
    start = args.split
    if start == 1 and len(src) > 4000:
        start = 4
    candidates = [start]
    while candidates[-1] < paragraph_count:
        next_split = 4 if candidates[-1] == 1 else candidates[-1] * 2
        candidates.append(min(next_split, paragraph_count))

    out = ""
    usage = []
    used_split = args.split
    for idx, n in enumerate(candidates):
        out, usage = run_split(n, allow_none=(idx == len(candidates) - 1))
        used_split = n
        if out is not None and "结论" in out:
            break
        print(f"split={n} produced no conclusion, escalating", flush=True)
        out = out or ""

    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(out, encoding="utf-8")

    severe = len(re.findall(r"(?m)^- 【[^】]*】", out.split("## 一般")[0]))
    total = len(re.findall(r"(?m)^- 【[^】]*】", out))
    verdict = "PASS" if "结论：PASS" in out else ("FAIL" if "结论：FAIL" in out else "UNKNOWN")
    print(
        f"{args.chunk}: {verdict} issues={total} severe={severe} split={used_split} -> {dest} usage {usage}"
    )
    if verdict == "UNKNOWN":
        sys.exit(1)


if __name__ == "__main__":
    main()
