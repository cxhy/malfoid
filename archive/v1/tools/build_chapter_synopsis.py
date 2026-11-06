#!/usr/bin/env python3
"""Generate per-chapter synopses from the polished Chinese text.

Usage:
    .venv/bin/python tools/build_chapter_synopsis.py works/<work> --volume 7
    .venv/bin/python tools/build_chapter_synopsis.py works/<work> --single

Reads translation/polished/vol_<N>.md (multi-volume) or book.md (single), splits
by top-level chapter headings, asks the local model for a short chapter synopsis,
and appends a "## 逐章梗概" section to the volume synopsis file:
    works/<work>/synopsis/vol_<N>.md   or works/<work>/synopsis.md

The chapter text is the only source; no canon plot is imported. The section is
rewritten in place (between the marker and EOF) so the script is re-runnable.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_synopsis import CN_NUM, call, clean, load_config  # noqa: E402

MARKER = "## 逐章梗概"

SYSTEM = "你是一位为封面和插图绘制准备素材的编辑。你只输出本章梗概，不写说明、前言或修改清单。"

RULES = """写本章梗概的要求：
1. 只依据下面提供的中文润色稿章节。禁止用原著（HP 正传）情节补全；本章没有写到的事件不要写。
2. 输出 Markdown，结构固定：
### 第N章：<章节标题>
- 主要剧情：（80-150 字，按顺序写本章发生了什么，人物关系如何变化）
- 可画画面：（1-2 个，写地点/时间/人物/动作/光线气氛/视觉元素，必须能直接入画）
- 本章结尾：（一句话，写本章最后实际写到的人物处境或情绪）
3. 人名统一：女性 Draco 为“德拉科妮娅·马尔福”，单独出现为“德拉科妮娅”；禁止“达西”。
4. 不写译者注，不解释背景，不保留英文原词。"""


def cn_to_int(cn: str) -> int:
    if cn == "十":
        return 10
    if cn.startswith("十"):
        return 10 + CN_NUM.index(cn[1:])
    if "十" in cn:
        tens, _, ones = cn.partition("十")
        return CN_NUM.index(tens) * 10 + (CN_NUM.index(ones) if ones else 0)
    return CN_NUM.index(cn)


def split_chapters(text: str) -> list[tuple[int, str, str]]:
    """Return (chapter number, heading text, body) from a polished volume file."""
    parts = re.split(r"(?m)^# ", text)
    chapters = []
    for part in parts[1:]:
        head, _, body = part.partition("\n")
        m = re.match(r"第([一二三四五六七八九十]+)章[：:]\s*(.*)", head.strip())
        if not m:
            continue
        chapters.append((cn_to_int(m.group(1)), m.group(2).strip(), body.strip()))
    return chapters


def build(work: Path, volume: int | None, single: bool, effort: str, max_tokens: int) -> Path:
    polished = work / "translation" / "polished"
    src = polished / "book.md" if single else polished / f"vol_{volume}.md"
    dest = work / "synopsis.md" if single else work / "synopsis" / f"vol_{volume}.md"
    if not src.is_file():
        sys.exit(f"missing polished text: {src}")
    if not dest.is_file():
        sys.exit(f"missing volume synopsis: {dest}")

    meta = json.loads((work / "meta.json").read_text(encoding="utf-8"))
    title = meta.get("title") or work.name
    chapters = split_chapters(src.read_text(encoding="utf-8"))
    if not chapters:
        sys.exit(f"no chapter headings found in {src}")

    base_url, api_key = load_config()
    blocks = []
    for num, heading, body in chapters:
        prompt = (
            f"{RULES}\n\n作品名：{title}\n本章：第{num}章 {heading}\n\n"
            f"中文润色稿本章正文：\n\n{body}"
        )
        out, usage = call(
            base_url,
            api_key,
            "DeepSeek-V4.1-Flash-1017-TensorFold-Zig",
            prompt,
            effort,
            max_tokens,
        )
        out = clean(out)
        if "主要剧情" not in out:
            out, usage = call(
                base_url,
                api_key,
                "DeepSeek-V4.1-Flash-1017-TensorFold-Zig",
                prompt,
                "none",
                max_tokens,
            )
            out = clean(out)
        if "主要剧情" not in out:
            sys.exit(f"chapter {num} synopsis looks invalid: {out[:120]!r}")
        blocks.append(out)
        print(f"chapter {num}: {len(out)} chars, usage {usage}", flush=True)

    section = MARKER + "\n\n" + "\n\n".join(blocks)
    text = dest.read_text(encoding="utf-8")
    prompt_marker = "## 插图/封面 prompt sheet"
    if MARKER in text:
        before, rest = text.split(MARKER, 1)
        tail_start = rest.find(prompt_marker)
        tail = rest[tail_start:].rstrip() if tail_start != -1 else ""
        text = before.rstrip() + "\n\n" + section
        if tail:
            text += "\n\n" + tail
        text += "\n"
    else:
        tail_start = text.find(prompt_marker)
        if tail_start != -1:
            text = (
                text[:tail_start].rstrip()
                + "\n\n"
                + section
                + "\n\n"
                + text[tail_start:].rstrip()
                + "\n"
            )
        else:
            text = text.rstrip() + "\n\n" + section + "\n"
    dest.write_text(text, encoding="utf-8")
    return dest


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("work_dir", type=Path)
    ap.add_argument("--volume", type=int)
    ap.add_argument("--single", action="store_true")
    ap.add_argument("--effort", default="low")
    ap.add_argument("--max-tokens", type=int, default=8192)
    args = ap.parse_args()
    if not args.single and args.volume is None:
        sys.exit("pass --volume N or --single")
    dest = build(args.work_dir, args.volume, args.single, args.effort, args.max_tokens)
    print(f"-> {dest}")


if __name__ == "__main__":
    main()
