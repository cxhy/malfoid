#!/usr/bin/env python3
"""Translate one chunk with the local OpenAI-compatible provider.

Usage:
    .venv/bin/python tools/translate_chunk.py <temp_dir> chunk0001 [--effort medium]

Reads <temp_dir>/<chunk>.md, <temp_dir>/prompt_ctx/<chunk>.md and
<temp_dir>/glossary.json; writes output_<chunk>.md and an empty
output_<chunk>.meta.json. Prints token usage and a length summary.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.request
from pathlib import Path

import prompt_profile
import term_filter

MODELS = Path.home() / ".pi/agent/models.json"
PROVIDER = "deepseek-v4-1-flash"

SYSTEM = (
    "你是一位中文文学译者，专门把英文同人小说译成简体中文。"
    "译文要读起来像中文原创小说，而不是英文句子的逐字替换。"
    "你只输出译文正文，不写任何说明、前言、后记或译注。"
)

RULES = """翻译要求：
1. 只输出译文正文，不要任何说明、标题外的注释或对话式内容。
2. 保持 Markdown 结构：原有的标题行、空行分段、斜体/加粗、链接一律保留；不要合并或拆分原文段落。
3. 术语表里出现的专有名词必须逐字使用指定译名，包括别名形式；人名、地名、学院、咒语一律不许自行变译。
4. 人物性别与代词按原文和术语表；性转角色使用术语表指定的代词，不得按名字自行推断。
5. 对话用中文引号“”，句末标点放在引号内；省略号用……，破折号用——；不用英文标点。
6. 避免翻译腔：按中文叙述节奏重组句子、调整语序，允许合并短句；但不得增删情节、不得改动人物言行、因果和语气强度。
7. 保留叙述者的语气和人物说话的口吻差异；脏话、粗鲁、幼稚的措辞照实译出，不要美化。
8. 不要添加译者注、不要解释文化背景、不要保留英文原词（术语表没有的专有名词按通行译法音译）。
9. 译文长度与原文大致相当，不要扩写成赏析，也不要压缩成梗概。
10. 译文中不得残留任何英文单词或短语（包括书名、咒语、专有名词），一律译成中文；Markdown 的标题符号和链接地址除外。"""


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
        "temperature": 0.6,
        "max_tokens": max_tokens,
        "reasoning_effort": effort,
    }
    req = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
    )
    with urllib.request.urlopen(req, timeout=300) as resp:
        data = json.loads(resp.read())
    choice = data["choices"][0]
    usage = data.get("usage", {})
    usage["finish_reason"] = choice.get("finish_reason")
    return choice["message"]["content"], usage


def clean(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```(?:markdown|md)?\s*\n", "", text)
    text = re.sub(r"\n```\s*$", "", text)
    text = re.sub(r"^(译文|翻译)[:：]\s*\n?", "", text)
    return text.strip() + "\n"


def reasoning_overflow(usage: dict, max_tokens: int) -> bool:
    """True when the model spent its whole completion budget on reasoning."""
    details = usage.get("completion_tokens_details") or {}
    reasoning = details.get("reasoning_tokens") or 0
    completion = usage.get("completion_tokens") or 0
    return (
        usage.get("finish_reason") == "length"
        or reasoning >= max_tokens * 0.95
        or completion >= max_tokens
    )


def split_text(text: str) -> list[str]:
    """Split markdown text into two halves at a paragraph boundary near the middle."""
    paras = [p for p in re.split(r"\n\s*\n", text) if p.strip()]
    if len(paras) < 2:
        return [text]
    total = sum(len(p) for p in paras)
    acc, cut = 0, 1
    for i, p in enumerate(paras):
        acc += len(p)
        if acc >= total / 2:
            cut = min(max(i + 1, 1), len(paras) - 1)
            break
    first = "\n\n".join(paras[:cut])
    second = "\n\n".join(paras[cut:])
    return [first, second]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("temp_dir", type=Path)
    ap.add_argument("chunk")
    ap.add_argument("--effort", default="low")
    ap.add_argument(
        "--model",
        default=os.environ.get("MALFOID_MODEL", "DeepSeek-V4.1-Flash-1017-TensorFold-Zig"),
    )
    ap.add_argument("--max-tokens", type=int, default=24000)
    args = ap.parse_args()

    temp = args.temp_dir
    src = (temp / f"{args.chunk}.md").read_text(encoding="utf-8")
    ctx = (temp / "prompt_ctx" / f"{args.chunk}.md").read_text(encoding="utf-8")
    glossary = json.loads((temp / "glossary.json").read_text(encoding="utf-8"))
    rules = prompt_profile.compose(RULES, "translation", work_dir=temp.parent)

    terms = term_filter.format_terms(term_filter.select_terms(glossary["terms"], src))

    def try_translate(text: str, effort: str):
        prompt = (
            f"{rules}\n\n"
            f"术语表：\n{terms}\n\n"
            f"以下是全局预扫描、最近译文、下一块开头和相邻原文的只读上下文，仅供判断代词、称呼、语气和伏笔，不要翻译、不要写入译文：\n{ctx}\n\n"
            f"待翻译正文：\n\n{text}"
        )
        en_words = len(re.findall(r"[A-Za-z']+", text))
        min_cjk = max(12, int(en_words * 0.35))
        for attempt in range(2):
            try:
                raw, usage = call(base_url, api_key, args.model, prompt, effort, args.max_tokens)
            except Exception as e:  # timeout / HTTP error: retry the same text
                print(f"attempt {attempt + 1} failed: {e}", flush=True)
                time.sleep(8)
                continue
            out = clean(raw)
            if len(re.findall(r"[\u4e00-\u9fff]", out)) >= min_cjk:
                return out
            if reasoning_overflow(usage, args.max_tokens):
                print(
                    f"attempt {attempt + 1} (effort={effort}): reasoning overflow",
                    flush=True,
                )
                break
            print(
                f"attempt {attempt + 1} (effort={effort}): output too short/empty "
                f"({len(out)} chars)",
                flush=True,
            )
            time.sleep(5)
        return None

    def translate_recursive(text: str):
        # Quality first: keep the configured effort and split smaller before
        # dropping reasoning. Long chunks start split so later paragraphs keep
        # the same attention budget; effort=none is only allowed for a single
        # paragraph, the smallest unit, and only after it still overflows.
        if len(text) > 6000:
            parts = split_text(text)
            if len(parts) >= 2:
                print(
                    f"proactively splitting {len(text)} chars into {len(parts)} parts", flush=True
                )
                outs = [translate_recursive(p) for p in parts]
                if all(o is not None for o in outs):
                    return "\n\n".join(o.strip() for o in outs) + "\n"
                return None
        out = try_translate(text, args.effort)
        if out is not None:
            return out
        paragraphs = [p for p in re.split(r"\n\s*\n", text) if p.strip()]
        if len(paragraphs) <= 1:
            if args.effort != "none":
                print("last resort: single paragraph retried with effort=none", flush=True)
                return try_translate(text, "none")
            return None
        parts = split_text(text)
        if len(parts) < 2:
            return None
        print(f"splitting into {len(parts)} part(s)", flush=True)
        outs = [translate_recursive(p) for p in parts]
        if all(o is not None for o in outs):
            return "\n\n".join(o.strip() for o in outs) + "\n"
        return None

    base_url, api_key = load_config()
    out = translate_recursive(src)
    if out is None:
        sys.exit(f"{args.chunk}: translation failed")
    (temp / f"output_{args.chunk}.md").write_text(out, encoding="utf-8")
    (temp / f"output_{args.chunk}.meta.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "new_entities": [],
                "alias_hypotheses": [],
                "attribute_hypotheses": [],
                "used_term_sources": [],
                "conflicts": [],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    en_words = len(re.findall(r"[A-Za-z']+", src))
    zh_chars = len(re.findall(r"[\u4e00-\u9fff]", out))
    print(
        f"{args.chunk}: in {en_words} en words -> out {zh_chars} zh chars (ratio {zh_chars / max(1, en_words):.2f})"
    )


if __name__ == "__main__":
    main()
