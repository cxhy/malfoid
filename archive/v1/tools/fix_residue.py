#!/usr/bin/env python3
"""Repair untranslated Latin-script words left inside a translated chunk.

Usage:
    .venv/bin/python tools/fix_residue.py works/<work>/work_temp chunk0001 [...]

For each chunk, finds lines with English residue, asks the local model to
rewrite only those lines into natural Chinese (keeping markdown and punctuation),
substitutes them in place, then prints the QA result.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / ".pi/skills/translate-book/scripts"))

from qa_translation import english_residue  # noqa: E402
from translate_chunk import call, load_config  # noqa: E402

SYSTEM = "你是中文小说译文的校订者。只输出要求的 JSON，不要解释。"

PROMPT = """下面是一段中文小说译文里残留英文单词的行。请把每一行改写成自然的中文：
- 把行内所有拉丁字母的单词/短语换成符合上下文的汉语表达（人名、地名用通行中文译名）。
- 其余内容、标点、Markdown 标记（如 *斜体*、引号）保持不变，不要增删情节。
- 不要改动没有英文残留的行。
- 输出严格的 JSON 对象，键是行号（字符串），值是改写后的整行文本；不要代码块、不要注释。

行列表：
{lines}
"""


def extract_json(text: str) -> dict:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("no JSON object found")
    data = json.loads(m.group(0))
    if not isinstance(data, dict):
        raise ValueError("not an object")
    return data


def fix_chunk(temp: Path, cid: str) -> bool:
    out_path = temp / f"output_{cid}.md"
    text = out_path.read_text(encoding="utf-8")
    hits = english_residue(text)
    if not hits:
        print(f"{cid}: no residue")
        return True

    numbered = "\n".join(f"{i}. {line}" for i, line in enumerate(hits, 1))
    base_url, api_key = load_config()
    raw, _ = call(
        base_url,
        api_key,
        "DeepSeek-V4.1-Flash-1017-TensorFold-Zig",
        PROMPT.format(lines=numbered),
        "low",
        6000,
    )
    try:
        mapping = extract_json(raw)
    except ValueError as e:
        print(f"{cid}: model returned unparsable JSON ({e}); leaving as is")
        return False

    lines = text.split("\n")
    replaced = 0
    for i, old in enumerate(hits, 1):
        new = mapping.get(str(i)) or mapping.get(i)
        if not isinstance(new, str) or not new.strip():
            continue
        new = new.strip()
        # Only replace if the model's rewrite no longer has Latin words.
        if re.search(r"[A-Za-z][A-Za-z'’-]{2,}", re.sub(r"(?m)^#{1,6} .*$", "", new)):
            continue
        for j, line in enumerate(lines):
            if line.strip() == old.strip():
                lines[j] = new
                replaced += 1
                break
    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"{cid}: replaced {replaced}/{len(hits)} line(s)")
    return replaced == len(hits)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("temp_dir", type=Path)
    ap.add_argument("chunks", nargs="*")
    args = ap.parse_args()
    temp = args.temp_dir
    chunks = args.chunks or [
        p.stem.removeprefix("output_") for p in sorted(temp.glob("output_chunk*.md"))
    ]
    ok = True
    for cid in chunks:
        if not (temp / f"output_{cid}.md").is_file():
            continue
        ok = fix_chunk(temp, cid) and ok
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
