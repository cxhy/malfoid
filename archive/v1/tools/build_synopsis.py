#!/usr/bin/env python3
"""Generate a per-volume synopsis for cover/illustration work.

Usage:
    .venv/bin/python tools/build_synopsis.py works/<work> --volumes 1,2
    .venv/bin/python tools/build_synopsis.py works/<work> --single

Reads the polished Chinese volume text (translation/polished/vol_<N>.md) or
book.md for a single-volume work, and writes:
    works/<work>/synopsis/vol_<N>.md   for multi-volume works
    works/<work>/synopsis.md           for a single-volume work

The synopsis follows the template in Green_and_Silver/synopsis/README.md and is
based only on the polished text — no canon plot is imported. It is an
independent deliverable, not part of the translation HTML/Markdown.
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
CN_NUM = ["零", "一", "二", "三", "四", "五", "六", "七", "八", "九", "十"]

SYSTEM = (
    "你是一位给封面和插图绘制用的分卷梗概编辑。你只输出梗概正文，不写说明、前言、后记或修改清单。"
)

RULES = """写梗概要求：
1. 只依据下面提供的中文润色稿。禁止用原著（HP 正传）的情节、人物关系、住所、结局来补全；润色稿没有写到的事件不要写。每一条剧情和场景都必须能在润色稿里找到对应内容，不确定的细节宁可不写。
2. 输出 Markdown，结构固定（卷号与年级用中文数字）：
# <作品名> — 第N卷（N年级）分卷梗概（封面/插图用）
## 第N卷
- 标题：
- 主要剧情：（严格 300-500 字，按时间顺序，写清德拉科妮娅与哈利的关系如何变化、关键冲突、结局）
- 关键场景：（4-6 个，每个写：地点/时间/人物/动作/光线气氛/可直接入画的视觉元素）
- 结尾状态：（只写本卷文本最后实际写到的人物关系、处境、情绪）
- 本卷情感基调：
- 可用封面意象：（3-6 个符号、道具、构图想法）
- 不宜在封面剧透的内容：
3. 人名统一：女性 Draco 为“德拉科妮娅·马尔福”，单独出现为“德拉科妮娅”；禁止“达西”。
4. 关键场景必须有可画的视觉元素（光线、构图、道具、动作），不能只写剧情摘要。
5. 不写译者注，不解释背景，不保留英文原词。"""


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
        "temperature": 0.3,
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


def clean(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```(?:markdown|md)?\s*\n", "", text)
    text = re.sub(r"\n```\s*$", "", text)
    return text.strip() + "\n"


def build(work: Path, volumes: list[int], single: bool, effort: str, max_tokens: int) -> list[Path]:
    meta = json.loads((work / "meta.json").read_text(encoding="utf-8"))
    title = meta.get("title") or work.name
    polished = work / "translation" / "polished"
    base_url, api_key = load_config()

    targets: list[tuple[int | None, Path, Path]] = []
    if single:
        src = polished / "book.md"
        targets.append((None, src, work / "synopsis.md"))
    else:
        for n in volumes:
            src = polished / f"vol_{n}.md"
            targets.append((n, src, work / "synopsis" / f"vol_{n}.md"))

    written = []
    for n, src, dest in targets:
        if not src.is_file():
            sys.exit(f"missing polished text: {src}")
        label = f"第{CN_NUM[n]}卷（{CN_NUM[n]}年级）" if n is not None else "整本"
        text = src.read_text(encoding="utf-8")
        prompt = (
            f"{RULES}\n\n"
            f"作品名：{title}\n"
            f"本卷：{label}（卷号/年级用中文数字，例如 第一卷（一年级））\n\n"
            f"中文润色稿：\n\n{text}"
        )
        out, usage = call(
            base_url, api_key, "DeepSeek-V4.1-Flash-1017-TensorFold-Zig", prompt, effort, max_tokens
        )
        out = clean(out)
        if "主要剧情" not in out or len(out) < 300:
            # reasoning overflow: retry without reasoning before giving up
            retry_out, retry_usage = call(
                base_url,
                api_key,
                "DeepSeek-V4.1-Flash-1017-TensorFold-Zig",
                prompt,
                "none",
                max_tokens,
            )
            retry_out = clean(retry_out)
            if "主要剧情" in retry_out and len(retry_out) >= 300:
                out = retry_out
                usage = {"primary": usage, "fallback": retry_usage}
        if "主要剧情" not in out or len(out) < 300:
            sys.exit(f"synopsis output looks invalid for {label}: {len(out)} chars")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(out, encoding="utf-8")
        written.append(dest)
        print(f"{label}: -> {dest} usage {usage}")
    return written


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("work_dir", type=Path)
    ap.add_argument("--volumes", help="comma-separated volume numbers, e.g. 1,2")
    ap.add_argument("--single", action="store_true", help="single-volume work: use book.md")
    ap.add_argument("--effort", default="low")
    ap.add_argument("--max-tokens", type=int, default=32768)
    args = ap.parse_args()

    volumes = [int(x) for x in args.volumes.split(",")] if args.volumes else []
    if not args.single and not volumes:
        sys.exit("pass --volumes N,N or --single")
    build(args.work_dir, volumes, args.single, args.effort, args.max_tokens)


if __name__ == "__main__":
    main()
