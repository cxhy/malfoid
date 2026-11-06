#!/usr/bin/env python3
"""Build a resumable whole-book prescan before translation (Wenyi-style).

Usage:
    .venv/bin/python tools/build_prescan.py works/<work> [--force] [--effort low]
    .venv/bin/python tools/build_prescan.py works/<work> --reuse-digests

Produces, under <work>/work_temp/prescan/:
    style_brief.md              narrative voice, dialogue habits, character voices
    book_synopsis.md            translation-only whole-book arc and future facts
    story_bible.md              HP-fanfic reference: canon divergence, character
                                registry, timeline, secrets/reveals, status
    chapter_digests/ch-NN.md    one digest per chapter (complete coverage)
    chunk_map.json              chunk -> chapter mapping for prompt context
    cache/digest-<sha>.md       content-addressed digest cache
    meta.json                   inputs, hashes, model usage, timestamps

The prescan is translation context, not a reader-facing deliverable. It is
built from the source text only; glossary seeding stays with the normal
translation flow.

Resumability follows Wenyi's book-understanding service: each chapter digest is
keyed by the source hash plus prompt/profile version, and the style brief,
synopsis and story bible are keyed by their own inputs. A rerun reuses verified
artifacts and only calls the model for missing or changed inputs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import prompt_profile
import term_filter

MODELS = Path.home() / ".pi/agent/models.json"
PROVIDER = "deepseek-v4-1-flash"
MODEL = "DeepSeek-V4.1-Flash-1017-TensorFold-Zig"
DIGEST_CHUNK = 8000
PRESCAN_VERSION = 2

SYSTEM = (
    "你是一位长篇小说的预扫描编辑，负责在翻译前建立全书理解。"
    "你只输出要求的结构化中文内容，不写说明、前言、后记或修改清单。"
)

MERGE_RULES = """把同一章的若干分段 digest 合并成一份完整章节 digest：
1. 只合并已有内容，不新增原文没有的事件。
2. 保持原 digest 的固定结构（剧情 / 人物与关系 / 时间线与设定 / 人物状态与知情范围 /
   魔法与专名 / 语气与对白 / 与前后章的衔接/伏笔）。
3. 去重、按时间顺序排列，总长 600–1000 字。"""

STYLE_RULES = """写全书 style brief 的要求：
1. 只依据提供的章节 digest 和原文样本，不套用原著文风。
2. 输出 Markdown，结构固定：
## 叙述视角与时态
## 叙述节奏与句长
## 对白习惯
## 人物声音（至少写德拉科妮娅、哈利、罗恩、赫敏、伏地魔/贝拉特里克斯等主要人物）
## 用词与语气禁忌
## 翻译策略建议
3. 人物声音要具体到用词、句长、称呼、语气强度；不要只写“骄傲”“温柔”这类标签。
4. 总长 800–1200 字。"""

SYNOPSIS_RULES = """写翻译用全书 synopsis 的要求：
1. 只依据提供的章节 digest，不套用原著情节；digest 没写的事件不要写。
2. 输出 Markdown，结构固定：
## 全书主线（按时间顺序）
## 人物关系弧线
## 关键转折与伏笔
## 翻译时必须知道的未来事实（避免代词、关系、结局翻错）
## 结尾状态
3. 这是给译者看的，不是封面梗概；允许写结局和关键反转。
4. 人名统一：女性 Draco 为“德拉科妮娅·马尔福”，单独出现为“德拉科妮娅”；禁止“达西”。
5. 总长 1200–1800 字。"""


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_config() -> tuple[str, str]:
    base = os.environ.get("MALFOID_BASE_URL")
    if base:
        return base.rstrip("/"), os.environ.get("MALFOID_API_KEY", "")
    cfg = json.loads(MODELS.read_text(encoding="utf-8"))["providers"][PROVIDER]
    return cfg["baseUrl"].rstrip("/"), cfg["apiKey"]


def call(
    base_url: str, api_key: str, prompt: str, effort: str, max_tokens: int
) -> tuple[str, dict]:
    body = {
        "model": MODEL,
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


def overflowed(usage: dict, max_tokens: int) -> bool:
    details = usage.get("completion_tokens_details") or {}
    reasoning = details.get("reasoning_tokens") or 0
    return usage.get("finish_reason") == "length" or reasoning >= max_tokens * 0.95


def ask(base_url: str, api_key: str, prompt: str, effort: str, max_tokens: int) -> tuple[str, dict]:
    out, usage = call(base_url, api_key, prompt, effort, max_tokens)
    out = clean(out)
    if overflowed(usage, max_tokens) or len(out) < 120:
        retry_out, retry_usage = call(base_url, api_key, prompt, "none", max_tokens)
        retry_out = clean(retry_out)
        if len(retry_out) > len(out):
            return retry_out, {
                "primary": usage,
                "fallback": retry_usage,
                "_overflow": overflowed(retry_usage, max_tokens),
            }
        return out, {"primary": usage, "fallback": retry_usage, "_overflow": True}
    return out, {**usage, "_overflow": overflowed(usage, max_tokens)}


def split_paragraphs(text: str, limit: int) -> list[str]:
    paras = [p.strip() for p in re.split(r"\n\s*\n", text.strip()) if p.strip()]
    parts: list[str] = []
    cur: list[str] = []
    size = 0
    for para in paras:
        if cur and size + len(para) > limit:
            parts.append("\n\n".join(cur))
            cur = []
            size = 0
        cur.append(para)
        size += len(para)
    if cur:
        parts.append("\n\n".join(cur))
    return parts or [text.strip()]


def chapter_files(work: Path) -> list[tuple[int, str, str]]:
    chapters_dir = work / "source" / "chapters"
    records: list[tuple[int, str, str]] = []
    if chapters_dir.is_dir():
        for p in sorted(chapters_dir.glob("ch-*.md")):
            text = p.read_text(encoding="utf-8")
            m = re.search(r"(?m)^#\s*Chapter\s+(\d+)\s*[:：]?\s*(.*)$", text)
            if not m:
                continue
            records.append((int(m.group(1)), m.group(2).strip(), text))
        if records:
            return sorted(records, key=lambda r: r[0])

    work_md = (work / "source" / "work.md").read_text(encoding="utf-8")
    parts = re.split(r"(?m)^#\s*Chapter\s+(\d+)\s*[:：]?\s*(.*)$", work_md)
    for i in range(1, len(parts), 3):
        records.append((int(parts[i]), parts[i + 1].strip(), parts[i + 2]))
    return sorted(records, key=lambda r: r[0])


def chunk_map(temp: Path) -> dict[str, int]:
    mapping: dict[str, int] = {}
    current = None
    for p in sorted(temp.glob("chunk*.md")):
        if p.name.startswith("output_"):
            continue
        text = p.read_text(encoding="utf-8")
        m = re.search(r"(?m)^#\s*Chapter\s+(\d+)", text)
        if m:
            current = int(m.group(1))
        if current is not None:
            mapping[p.stem] = current
    return mapping


def read_json(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def digest_part(
    base_url: str,
    api_key: str,
    digest_rules: str,
    part: str,
    effort: str,
    max_tokens: int,
) -> tuple[str, list[dict]]:
    prompt = f"{digest_rules}\n\n本章原文：\n\n{part}"
    out, usage = ask(base_url, api_key, prompt, effort, max_tokens)
    if out.strip() and not usage.get("_overflow"):
        return out, [usage]
    # The reasoning budget consumed the whole completion. Split the source at a
    # paragraph boundary and digest each half, then merge the digests.
    paras = [p.strip() for p in re.split(r"\n\s*\n", part.strip()) if p.strip()]
    if len(paras) > 1:
        mid = len(paras) // 2
        left, left_usage = digest_part(
            base_url,
            api_key,
            digest_rules,
            "\n\n".join(paras[:mid]),
            effort,
            max_tokens,
        )
        right, right_usage = digest_part(
            base_url,
            api_key,
            digest_rules,
            "\n\n".join(paras[mid:]),
            effort,
            max_tokens,
        )
        if left.strip() and right.strip():
            merge_prompt = f"{MERGE_RULES}\n\n分段 digest：\n\n{left}\n\n---\n\n{right}"
            merged, merge_usage = ask(base_url, api_key, merge_prompt, effort, max_tokens)
            if merged.strip() and not merge_usage.get("_overflow"):
                return merged, left_usage + right_usage + [merge_usage]
        return "", left_usage + right_usage + [usage]
    return out, [usage]


def digest_chapter(
    base_url: str,
    api_key: str,
    chapter_text: str,
    effort: str,
    max_tokens: int,
    work: Path,
) -> tuple[str, list[dict]]:
    digest_rules = prompt_profile.section("prescan_digest", work_dir=work)
    parts = split_paragraphs(chapter_text, DIGEST_CHUNK)
    usages: list[dict] = []
    digests: list[str] = []
    for part in parts:
        out, part_usage = digest_part(base_url, api_key, digest_rules, part, effort, max_tokens)
        if not out.strip() or any(u.get("_overflow") for u in part_usage):
            return "", usages + part_usage
        digests.append(out)
        usages.extend(part_usage)
    if len(digests) == 1:
        return digests[0], usages
    prompt = f"{MERGE_RULES}\n\n分段 digest：\n\n" + "\n\n---\n\n".join(digests)
    out, usage = ask(base_url, api_key, prompt, effort, max_tokens)
    usages.append(usage)
    if not out.strip() or usage.get("_overflow"):
        return "", usages
    return out, usages


def samples(chapters: list[tuple[int, str, str]]) -> str:
    if not chapters:
        return ""
    picks = [chapters[0], chapters[len(chapters) // 2], chapters[-1]]
    chunks = []
    for _, title, text in picks:
        body = re.sub(r"(?m)^#.*$", "", text).strip()
        chunks.append(f"### {title}\n\n{body[:1200]}")
    return "\n\n".join(chunks)


def glossary_lines(work: Path, text: str) -> str:
    path = work / "work_temp" / "glossary.json"
    if not path.is_file():
        return ""
    try:
        terms = json.loads(path.read_text(encoding="utf-8")).get("terms", [])
    except json.JSONDecodeError:
        return ""
    # Only terms that actually occur in the digests are useful for the story
    # bible; the full glossary would add thousands of unrelated tokens.
    return term_filter.format_terms(term_filter.select_terms(terms, text))


def build_story_bible(
    base_url: str,
    api_key: str,
    digests_text: str,
    style_brief: str,
    glossary: str,
    effort: str,
    max_tokens: int,
    work: Path,
) -> tuple[str, dict]:
    rules = prompt_profile.section("story_bible", work_dir=work)
    prompt = (
        f"{rules}\n\n"
        f"## 全书风格 brief\n\n{style_brief or '（无）'}\n\n"
        f"## 章节 digest\n\n{digests_text}\n\n"
        f"## 术语表（只读；译名以此为准）\n\n{glossary or '（无）'}"
    )
    return ask(base_url, api_key, prompt, effort, max_tokens)


def build(work: Path, effort: str, force: bool, reuse_digests: bool) -> None:
    temp = work / "work_temp"
    out_dir = temp / "prescan"
    meta_path = out_dir / "meta.json"
    old_meta = {} if force else read_json(meta_path)
    old_chapters = {
        int(row.get("chapter", -1)): row
        for row in old_meta.get("chapters", [])
        if isinstance(row, dict)
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "chapter_digests").mkdir(exist_ok=True)
    (out_dir / "cache").mkdir(exist_ok=True)

    chapters = chapter_files(work)
    if not chapters:
        sys.exit("no numbered chapters found in source/work.md or source/chapters/")
    base_url, api_key = load_config()
    max_tokens = 16000
    profile = prompt_profile.load_profile(work_dir=work)
    profile_id = profile["id"]
    profile_version = profile.get("version", 1)

    digest_records = []
    digest_texts: list[str] = []
    reused = 0
    for number, title, text in chapters:
        source_hash = sha(text)
        digest_path = out_dir / "chapter_digests" / f"ch-{number:02d}.md"
        cache_path = out_dir / "cache" / f"digest-{source_hash}.md"
        cached = old_chapters.get(number, {})
        reusable = (
            not force
            and cached.get("source_sha256") == source_hash
            and cached.get("prompt_version") == PRESCAN_VERSION
            and cached.get("profile") == profile_id
            and cached.get("profile_version") == profile_version
            and digest_path.is_file()
            and digest_path.read_text(encoding="utf-8").strip()
        )
        if reusable:
            digest = digest_path.read_text(encoding="utf-8").strip() + "\n"
            usages = cached.get("usage", [])
            reused += 1
            print(f"chapter {number}: reused digest", flush=True)
        elif (
            reuse_digests
            and digest_path.is_file()
            and digest_path.read_text(encoding="utf-8").strip()
        ):
            digest = digest_path.read_text(encoding="utf-8").strip() + "\n"
            usages = []
            reused += 1
            print(f"chapter {number}: reused existing digest (unverified)", flush=True)
        else:
            print(f"chapter {number}: {title}", flush=True)
            digest, usages = digest_chapter(base_url, api_key, text, effort, max_tokens, work)
            digest_path.write_text(digest, encoding="utf-8")
        cache_path.write_text(digest, encoding="utf-8")
        digest_records.append(
            {
                "chapter": number,
                "title": title,
                "source_sha256": source_hash,
                "digest_sha256": sha(digest),
                "digest_chars": len(digest),
                "prompt_version": PRESCAN_VERSION,
                "profile": profile_id,
                "profile_version": profile_version,
                "usage": usages,
            }
        )
        digest_texts.append(f"### 第{number}章 {title}\n\n{digest.strip()}")
        # Save a partial manifest after each chapter so an interrupted run can
        # reuse completed digests instead of regenerating them.
        write_json(
            meta_path,
            {
                "version": PRESCAN_VERSION,
                "profile": profile_id,
                "profile_version": profile_version,
                "chapters": digest_records,
            },
        )

    all_digests = "\n\n".join(digest_texts)
    style_input = sha(all_digests + samples(chapters) + profile_id + str(PRESCAN_VERSION))
    style_meta = old_meta.get("style", {}) if not force else {}
    style_brief = ""
    style_usage: dict | list = {}
    style_path = out_dir / "style_brief.md"
    if (
        style_meta.get("input_sha256") == style_input
        and style_path.is_file()
        and style_path.read_text(encoding="utf-8").strip()
    ):
        style_brief = style_path.read_text(encoding="utf-8").strip() + "\n"
        style_usage = style_meta.get("usage", {})
        print("style brief: reused", flush=True)
    else:
        style_prompt = (
            f"{STYLE_RULES}\n\n章节 digest：\n\n{all_digests}\n\n原文样本：\n\n{samples(chapters)}"
        )
        style_brief, style_usage = ask(base_url, api_key, style_prompt, effort, max_tokens)
        if style_usage.get("_overflow") or len(style_brief.strip()) < 120:
            raise RuntimeError(
                "style brief overflowed even with reasoning disabled; "
                "reduce the digest size or raise --max-tokens"
            )
        style_path.write_text(style_brief, encoding="utf-8")

    synopsis_input = sha(all_digests + profile_id + str(PRESCAN_VERSION))
    synopsis_meta = old_meta.get("synopsis", {}) if not force else {}
    synopsis = ""
    synopsis_usage: dict | list = {}
    synopsis_path = out_dir / "book_synopsis.md"
    if (
        synopsis_meta.get("input_sha256") == synopsis_input
        and synopsis_path.is_file()
        and synopsis_path.read_text(encoding="utf-8").strip()
    ):
        synopsis = synopsis_path.read_text(encoding="utf-8").strip() + "\n"
        synopsis_usage = synopsis_meta.get("usage", {})
        print("book synopsis: reused", flush=True)
    else:
        synopsis_prompt = f"{SYNOPSIS_RULES}\n\n章节 digest：\n\n{all_digests}"
        synopsis, synopsis_usage = ask(base_url, api_key, synopsis_prompt, effort, max_tokens)
        if synopsis_usage.get("_overflow") or len(synopsis.strip()) < 120:
            raise RuntimeError(
                "book synopsis overflowed even with reasoning disabled; "
                "reduce the digest size or raise --max-tokens"
            )
        synopsis_path.write_text(synopsis, encoding="utf-8")

    glossary = glossary_lines(work, all_digests + style_brief)
    bible_input = sha(all_digests + style_brief + glossary + profile_id + str(profile_version))
    bible_meta = old_meta.get("story_bible", {}) if not force else {}
    story_bible = ""
    bible_usage: dict | list = {}
    bible_path = out_dir / "story_bible.md"
    if (
        bible_meta.get("input_sha256") == bible_input
        and bible_path.is_file()
        and bible_path.read_text(encoding="utf-8").strip()
    ):
        story_bible = bible_path.read_text(encoding="utf-8").strip() + "\n"
        bible_usage = bible_meta.get("usage", {})
        print("story bible: reused", flush=True)
    else:
        story_bible, bible_usage = build_story_bible(
            base_url, api_key, all_digests, style_brief, glossary, effort, max_tokens, work
        )
        if bible_usage.get("_overflow") or len(story_bible.strip()) < 120:
            raise RuntimeError(
                "story bible overflowed even with reasoning disabled; "
                "reduce the digest size or raise --max-tokens"
            )
        bible_path.write_text(story_bible, encoding="utf-8")

    mapping = chunk_map(temp)
    write_json(out_dir / "chunk_map.json", mapping)

    meta = {
        "version": PRESCAN_VERSION,
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "model": MODEL,
        "effort": effort,
        "profile": profile_id,
        "profile_version": profile_version,
        "chapters": digest_records,
        "style": {
            "input_sha256": style_input,
            "chars": len(style_brief),
            "usage": style_usage,
        },
        "synopsis": {
            "input_sha256": synopsis_input,
            "chars": len(synopsis),
            "usage": synopsis_usage,
        },
        "story_bible": {
            "input_sha256": bible_input,
            "chars": len(story_bible),
            "usage": bible_usage,
        },
        "chunk_map": mapping,
        "reused_digests": reused,
    }
    write_json(meta_path, meta)
    print(
        f"prescan -> {out_dir} ({len(chapters)} chapters, {reused} reused, "
        f"story bible {len(story_bible)} chars)",
        flush=True,
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("work_dir", type=Path)
    ap.add_argument("--effort", default="low")
    ap.add_argument("--force", action="store_true", help="regenerate every artifact")
    ap.add_argument(
        "--reuse-digests",
        action="store_true",
        help="reuse existing chapter_digests files without verifying their source hash",
    )
    args = ap.parse_args()
    build(args.work_dir, args.effort, args.force, args.reuse_digests)


if __name__ == "__main__":
    main()
