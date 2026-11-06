#!/usr/bin/env python3
"""Whole-book continuity review for Harry Potter fanfiction.

Usage:
    .venv/bin/python tools/review_book.py works/<work> [chunk0001 ...]
    .venv/bin/python tools/review_book.py works/<work> --force --effort low

This is the cross-chapter counterpart to ``proofread_chunk.py``. Proofreading
checks one chunk against its English source; this tool checks the book as a
whole for the failures that only appear across chapters:

- terminology and forms-of-address drift;
- character gender/pronoun drift (female Draco stays female);
- timeline, age, school-year and event-order contradictions;
- character status contradictions (alive/dead, injured, house, blood status);
- reveal timing (who knows what, and when they learn it);
- canon-divergence consistency (the fic's AU facts are not "corrected" to canon);
- voice and content-intensity drift.

It writes one report per chunk to ``translation/review/chunkXXXX.md`` and a
book-level summary to ``translation/review/book_review.md``. Reports are
advisory: they name the location and the minimal fix, and never rewrite text.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import urllib.request
from pathlib import Path

import prompt_profile

MODELS = Path.home() / ".pi/agent/models.json"
PROVIDER = "deepseek-v4-1-flash"

SYSTEM = (
    "你是一位哈利波特同人的全书连续性审查编辑。你对照英文原文、中文译文、"
    "全书 story bible、章节 digest 和跨章证据，只找跨章漂移与前后矛盾，"
    "不改写全文，只给出定位和最小修改建议。"
)

SEVERITIES = ("严重", "一般", "轻微")
TYPES = (
    "continuity",
    "terminology",
    "pronoun",
    "timeline",
    "status",
    "knowledge",
    "canon",
    "voice",
    "fidelity",
    "structure",
)


def load_config() -> tuple[str, str]:
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


def clean(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```(?:markdown|md|json)?\s*\n", "", text)
    text = re.sub(r"\n```\s*$", "", text)
    return text.strip()


def overflowed(usage: dict, max_tokens: int) -> bool:
    details = usage.get("completion_tokens_details") or {}
    reasoning = details.get("reasoning_tokens") or 0
    completion = usage.get("completion_tokens") or 0
    return (
        usage.get("finish_reason") == "length"
        or reasoning >= max_tokens * 0.95
        or completion >= max_tokens
    )


def read_optional(path: Path) -> str:
    return path.read_text(encoding="utf-8").strip() if path.is_file() else ""


def natural(name: str) -> int:
    m = re.search(r"(\d+)$", name)
    return int(m.group(1)) if m else 0


def chunk_ids(temp: Path) -> list[str]:
    return sorted(
        (p.stem for p in temp.glob("chunk*.md") if not p.name.startswith("output_")),
        key=natural,
    )


def polished_text(work: Path, temp: Path, chunk: str) -> str:
    path = work / "translation" / "polished" / f"{chunk}.md"
    if path.is_file():
        return path.read_text(encoding="utf-8")
    return read_optional(temp / f"output_{chunk}.md")


def paragraphs(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"\n\s*\n", text.strip()) if p.strip()]


def split_parts(text: str, n: int) -> list[str]:
    paras = paragraphs(text)
    if n <= 1 or len(paras) <= 1:
        return [text.strip()]
    n = min(n, len(paras))
    bounds = [round(k * len(paras) / n) for k in range(n + 1)]
    bounds[0] = 0
    bounds[-1] = len(paras)
    return ["\n\n".join(paras[bounds[k] : bounds[k + 1]]) for k in range(n)]


def snippet(text: str, needle: str, radius: int = 90) -> str:
    """Return a bounded context window around the first occurrence of needle."""
    if not needle:
        return ""
    index = text.find(needle)
    if index < 0:
        return ""
    start = max(0, index - radius)
    end = min(len(text), index + len(needle) + radius)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(text) else ""
    return prefix + text[start:end].replace("\n", " ") + suffix


def term_matches(text: str, term: dict) -> bool:
    """Match a glossary source or alias as a whole Latin word/phrase."""
    forms = [term.get("source", "")] + list(term.get("aliases") or [])
    for form in forms:
        if not form:
            continue
        if re.fullmatch(r"[\x00-\x7f]+", form):
            if re.search(rf"(?<![A-Za-z]){re.escape(form)}(?![A-Za-z])", text):
                return True
        elif form in text:
            return True
    return False


def deterministic_issues(source: str, target: str, terms: list[dict], profile: dict) -> list[dict]:
    """Definite checks that need no model: structure, forbidden names, term drift."""
    issues: list[dict] = []
    src_paras = len(paragraphs(source))
    tgt_paras = len(paragraphs(target))
    tolerance = max(2, int(src_paras * 0.15))
    if abs(src_paras - tgt_paras) > tolerance:
        issues.append(
            {
                "severity": "严重",
                "type": "structure",
                "detail": f"段落数偏差过大：原文 {src_paras} 段，译文 {tgt_paras} 段",
                "translation": "",
                "source": "",
                "chapters": "",
                "suggestion": "核对是否漏译、合并或拆分段落；按原文恢复段落边界。",
                "definite": True,
            }
        )

    for name in profile.get("forbidden_names", []):
        if isinstance(name, str) and name in target:
            issues.append(
                {
                    "severity": "严重",
                    "type": "terminology",
                    "detail": f"出现禁用译名“{name}”",
                    "translation": snippet(target, name),
                    "source": "",
                    "chapters": "",
                    "suggestion": f"按术语表改为女性马尔福的指定译名，删除“{name}”。",
                    "definite": True,
                }
            )

    female = profile.get("female_draco") or {}
    short = female.get("short", "")
    if short:
        # A bare 德拉科 is the male form; 德拉科妮娅 contains it, so exclude that.
        for m in re.finditer(r"德拉科(?!妮娅)", target):
            issues.append(
                {
                    "severity": "严重",
                    "type": "pronoun",
                    "detail": "女性马尔福被写成男性形式“德拉科”",
                    "translation": snippet(target, m.group(0)),
                    "source": "",
                    "chapters": "",
                    "suggestion": f"改为“{short}”或“{female.get('full', short)}”，代词用“她”。",
                    "definite": True,
                }
            )

    for term in terms:
        source_form = term.get("source", "")
        target_form = term.get("target", "")
        if not source_form or not target_form:
            continue
        if not term_matches(source, term):
            continue
        if target_form not in target:
            issues.append(
                {
                    "severity": "轻微",
                    "type": "terminology",
                    "detail": f"原文出现“{source_form}”，译文未见指定译名“{target_form}”",
                    "translation": "",
                    "source": snippet(source, source_form),
                    "chapters": "",
                    "suggestion": "核对是否用代词替代；若直译了该专名，改为术语表译名。",
                    "definite": False,
                }
            )
    return issues


def occurrence_evidence(
    temp: Path,
    work: Path,
    ids: list[str],
    current: str,
    source: str,
    terms: list[dict],
    limit_chars: int = 5000,
) -> str:
    """Collect bounded cross-chapter occurrences for terms present in this chunk."""
    used = 0
    lines: list[str] = []
    seen: set[str] = set()
    for term in terms:
        if used >= limit_chars:
            break
        source_form = term.get("source", "")
        target_form = term.get("target", "")
        if not source_form or source_form in seen:
            continue
        if not term_matches(source, term):
            continue
        seen.add(source_form)
        lines.append(f"- {source_form} → {target_form}")
        used += len(lines[-1]) + 1
        hits = 0
        for cid in ids:
            if cid == current or hits >= 2 or used >= limit_chars:
                continue
            src_other = (temp / f"{cid}.md").read_text(encoding="utf-8")
            tgt_other = polished_text(work, temp, cid)
            src_snip = snippet(src_other, source_form, 60)
            tgt_snip = snippet(tgt_other, target_form, 60)
            if not src_snip and not tgt_snip:
                continue
            line = f"  - {cid} 原文：{src_snip or '（未出现）'}｜译文：{tgt_snip or '（未出现）'}"
            lines.append(line)
            used += len(line) + 1
            hits += 1
    return "\n".join(lines)


def chapter_for(chunk: str, chunk_map: dict[str, int]) -> int | None:
    return chunk_map.get(chunk)


def build_prompt(
    work: Path,
    temp: Path,
    chunk: str,
    source: str,
    target: str,
    terms: list[dict],
    ids: list[str],
    candidates: list[dict],
    chapter_map: dict[str, int],
) -> str:
    prescan = temp / "prescan"
    bible = read_optional(prescan / "story_bible.md")
    chapter = chapter_for(chunk, chapter_map)
    digest = ""
    if chapter is not None:
        digest = read_optional(prescan / "chapter_digests" / f"ch-{chapter:02d}.md")
    relevant = [
        {
            "source": t.get("source", ""),
            "target": t.get("target", ""),
            "aliases": t.get("aliases", []),
            "notes": t.get("notes", ""),
        }
        for t in terms
        if term_matches(source, t)
    ]
    evidence = occurrence_evidence(temp, work, ids, chunk, source, terms)
    rules = prompt_profile.section("book_review", work_dir=work)
    chapter_label = f"第 {chapter} 章" if chapter is not None else "章节未映射"
    return (
        f"{rules}\n\n"
        "只输出 JSON，不要输出 Markdown 报告、解释或前言。JSON 结构：\n"
        '{"issues":[{"severity":"严重|一般|轻微","type":"continuity|terminology|pronoun|'
        'timeline|status|knowledge|canon|voice|fidelity|structure","index":0,'
        '"detail":"问题描述","translation":"译文短句","source":"原文短句",'
        '"chapters":"第 N 章 → 第 M 章","suggestion":"最小修改建议",'
        '"consistency":{"subject_source":"需要跨章一致的原文主体","kind":"term|pronoun|fixed",'
        '"proposed_value":"建议的统一值"}}],"complete":true}\n\n'
        f"## 程序预检候选（可能误报；确认或驳回，驳回就不要写入 issues）\n"
        f"{json.dumps(candidates, ensure_ascii=False, indent=2)}\n\n"
        f"## 全书 story bible（只读参考）\n{bible or '（未生成）'}\n\n"
        f"## 本章 digest（{chapter_label}）\n{digest or '（未生成）'}\n\n"
        f"## 本块相关术语（只读）\n"
        + ("\n".join(f"- {t['source']} → {t['target']}" for t in relevant) or "（无）")
        + "\n\n"
        f"## 跨章出现证据（只读；用于核对漂移，不是待译文本）\n{evidence or '（无）'}\n\n"
        f"## 当前块 {chunk}（{chapter_label}）\n"
        f"### 英文原文\n\n{source}\n\n### 中文润色稿\n\n{target}\n\n"
        "审查本块与全书的一致性。issues 为空数组时也必须写 complete:true。"
    )


def extract_json(text: str) -> dict | None:
    cleaned = clean(text)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def normalize_issues(data: dict) -> list[dict]:
    issues = []
    for raw in data.get("issues") or []:
        if not isinstance(raw, dict):
            continue
        severity = str(raw.get("severity", "一般")).strip()
        if severity not in SEVERITIES:
            severity = "一般"
        issue_type = str(raw.get("type", "continuity")).strip()
        if issue_type not in TYPES:
            issue_type = "continuity"
        consistency = raw.get("consistency")
        if not isinstance(consistency, dict):
            consistency = {}
        issues.append(
            {
                "severity": severity,
                "type": issue_type,
                "index": raw.get("index", 0),
                "detail": str(raw.get("detail", "")).strip(),
                "translation": str(raw.get("translation", "")).strip(),
                "source": str(raw.get("source", "")).strip(),
                "chapters": str(raw.get("chapters", "")).strip(),
                "suggestion": str(raw.get("suggestion", "")).strip(),
                "consistency": {
                    "subject_source": str(consistency.get("subject_source", "")).strip(),
                    "kind": str(consistency.get("kind", "")).strip(),
                    "proposed_value": str(consistency.get("proposed_value", "")).strip(),
                },
            }
        )
    return issues


def report_markdown(chunk: str, issues: list[dict]) -> str:
    lines = [f"# {chunk} 全书连续性审查", ""]
    for severity, title in (
        ("严重", "## 严重（前后矛盾、提前剧透、人物状态错误）"),
        ("一般", "## 一般（术语/称呼漂移、时间线偏差、语气漂移）"),
        ("轻微", "## 轻微（指代模糊、可读性）"),
    ):
        lines.append(title)
        rows = [i for i in issues if i["severity"] == severity]
        if not rows:
            lines.append("无")
        for issue in rows:
            location = f"块：{chunk}"
            if issue.get("chapters"):
                location += f"｜涉及：{issue['chapters']}"
            lines.append(
                f"- 【{issue['type']}】{location}｜译文：“{issue['translation']}”｜"
                f"原文：“{issue['source']}”｜问题：{issue['detail']}｜建议：{issue['suggestion']}"
            )
        lines.append("")
    severe_or_general = any(i["severity"] in ("严重", "一般") for i in issues)
    lines.append(f"结论：{'FAIL' if severe_or_general else 'PASS'}")
    return "\n".join(lines) + "\n"


def group_conflicts(issues_by_chunk: dict[str, list[dict]]) -> list[dict]:
    """Find incompatible values proposed for one consistency subject across chunks."""
    groups: dict[str, list[dict]] = {}
    for chunk, issues in issues_by_chunk.items():
        for issue in issues:
            consistency = issue.get("consistency") or {}
            subject = consistency.get("subject_source", "")
            proposed = consistency.get("proposed_value", "")
            kind = consistency.get("kind", "")
            if not subject or not proposed:
                continue
            key = f"{kind}:{subject}"
            row = dict(issue)
            row["chunk"] = chunk
            groups.setdefault(key, []).append(row)
    conflicts = []
    for key, rows in groups.items():
        chunks = {row["chunk"] for row in rows}
        values = {row["consistency"]["proposed_value"] for row in rows}
        if len(chunks) < 2 or len(values) < 2:
            continue
        conflicts.append(
            {
                "key": key,
                "subject": rows[0]["consistency"]["subject_source"],
                "kind": rows[0]["consistency"]["kind"],
                "values": sorted(values),
                "chunks": sorted(chunks, key=natural),
            }
        )
    conflicts.sort(key=lambda row: (row["key"], row["chunks"]))
    return conflicts


def merge_book_report(
    work: Path,
    reports: dict[str, str],
    conflicts: list[dict],
    issues_by_chunk: dict[str, list[dict]],
) -> str:
    lines = ["# 全书连续性审查汇总", ""]
    total = {s: 0 for s in SEVERITIES}
    failed = []
    for chunk in sorted(reports, key=natural):
        text = reports[chunk]
        for severity in SEVERITIES:
            section = re.search(rf"(?ms)^## {severity}.*?\n(.*?)(?=^## |\Z)", text)
            if section:
                total[severity] += len(
                    [line for line in section.group(1).splitlines() if line.startswith("- ")]
                )
        verdict = "FAIL" if "结论：FAIL" in text else "PASS"
        if verdict == "FAIL":
            failed.append(chunk)

    lines.append("## 统计")
    lines.append(
        f"- 审查块数：{len(reports)}；严重 {total['严重']}；一般 {total['一般']}；"
        f"轻微 {total['轻微']}；FAIL {len(failed)}"
    )
    if failed:
        lines.append(f"- FAIL 块：{', '.join(failed)}")
    lines.append("")

    lines.append("## 跨章冲突（需要人工裁定）")
    if not conflicts:
        lines.append("无")
    for conflict in conflicts:
        lines.append(
            f"- {conflict['kind']}｜{conflict['subject']}｜候选值："
            f"{' / '.join(conflict['values'])}｜涉及：{', '.join(conflict['chunks'])}"
        )
    lines.append("")

    lines.append("## 分块报告")
    for chunk in sorted(reports, key=natural):
        verdict = "FAIL" if "结论：FAIL" in reports[chunk] else "PASS"
        count = len(issues_by_chunk.get(chunk, []))
        lines.append(f"- {chunk}：{verdict}（{count} 条）→ translation/review/{chunk}.md")
    lines.append("")
    lines.append("说明：本报告只做跨章连续性审查，逐句忠实度以 translation/proofread/ 为准。")
    return "\n".join(lines) + "\n"


def ask_review(
    base_url: str,
    api_key: str,
    model: str,
    prompt: str,
    effort: str,
    max_tokens: int,
) -> tuple[dict | None, dict, bool]:
    raw, usage = call(base_url, api_key, model, prompt, effort, max_tokens)
    data = extract_json(raw)
    overflow = overflowed(usage, max_tokens)
    if data is not None and not overflow:
        return data, usage, False
    # One retry without reasoning; structured output is more important than thinking budget.
    retry_raw, retry_usage = call(base_url, api_key, model, prompt, "none", max_tokens)
    data = extract_json(retry_raw)
    overflow = overflowed(retry_usage, max_tokens)
    return data, {"primary": usage, "fallback": retry_usage}, overflow or data is None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("work_dir", type=Path)
    ap.add_argument("chunks", nargs="*")
    ap.add_argument("--effort", default="low")
    ap.add_argument(
        "--model",
        default=os.environ.get("MALFOID_MODEL", "DeepSeek-V4.1-Flash-1017-TensorFold-Zig"),
    )
    ap.add_argument("--max-tokens", type=int, default=32768)
    ap.add_argument("--split", type=int, default=1, help="split a chunk into N parts")
    ap.add_argument("--force", action="store_true", help="overwrite existing reports")
    args = ap.parse_args()

    work = args.work_dir
    temp = work / "work_temp"
    out_dir = work / "translation" / "review"
    out_dir.mkdir(parents=True, exist_ok=True)
    ids = chunk_ids(temp)
    todo = args.chunks or ids
    todo = [c if c.startswith("chunk") else f"chunk{c}" for c in todo]
    glossary = json.loads((temp / "glossary.json").read_text(encoding="utf-8"))
    terms = glossary.get("terms", [])
    profile = prompt_profile.load_profile(work_dir=work)
    chunk_map_path = temp / "prescan" / "chunk_map.json"
    chunk_map = {}
    if chunk_map_path.is_file():
        chunk_map = json.loads(chunk_map_path.read_text(encoding="utf-8"))
    base_url, api_key = load_config()

    reports: dict[str, str] = {}
    issues_by_chunk: dict[str, list[dict]] = {}
    for chunk in todo:
        dest = out_dir / f"{chunk}.md"
        if dest.is_file() and not args.force:
            print(f"{chunk}: report exists, skipped")
            reports[chunk] = dest.read_text(encoding="utf-8")
            issues_path = out_dir / "issues" / f"{chunk}.json"
            if issues_path.is_file():
                try:
                    issues_by_chunk[chunk] = json.loads(issues_path.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    issues_by_chunk[chunk] = []
            continue

        source = (temp / f"{chunk}.md").read_text(encoding="utf-8")
        target = polished_text(work, temp, chunk)
        if not target.strip():
            print(f"{chunk}: no translation, skipped")
            continue
        candidates = deterministic_issues(source, target, terms, profile)
        paragraph_count = max(1, len(paragraphs(source)))
        ceiling = max(args.split, min(paragraph_count, 8))
        split_candidates = [args.split]
        while split_candidates[-1] < ceiling:
            split_candidates.append(min(split_candidates[-1] * 2, ceiling))

        issues: list[dict] = []
        succeeded = False
        for n in split_candidates:
            parts = split_parts(source, n)
            target_parts = split_parts(target, n)
            part_issues: list[dict] = []
            ok = True
            for src_part, tgt_part in zip(parts, target_parts):
                prompt = build_prompt(
                    work,
                    temp,
                    chunk,
                    src_part,
                    tgt_part,
                    terms,
                    ids,
                    candidates,
                    chunk_map,
                )
                data, usage, overflow = ask_review(
                    base_url, api_key, args.model, prompt, args.effort, args.max_tokens
                )
                if data is None or overflow:
                    ok = False
                    break
                part_issues.extend(normalize_issues(data))
            if ok:
                issues = part_issues
                succeeded = True
                print(f"{chunk}: reviewed {len(part_issues)} model issue(s) at split={n}")
                break
            print(f"{chunk}: split={n} overflowed or returned no JSON; escalating")

        # Definite program checks always survive; model-unconfirmed term-drift candidates are
        # dropped. If the model failed entirely, keep the candidates for human verification.
        definite = [item for item in candidates if item.get("definite")]
        if succeeded:
            issues = definite + issues
        else:
            issues = candidates
        reports[chunk] = report_markdown(chunk, issues)
        dest.write_text(reports[chunk], encoding="utf-8")
        issues_dir = out_dir / "issues"
        issues_dir.mkdir(exist_ok=True)
        (issues_dir / f"{chunk}.json").write_text(
            json.dumps(issues, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        issues_by_chunk[chunk] = issues
        print(f"{chunk}: {reports[chunk].rstrip().splitlines()[-1]} -> {dest}")

    conflicts = group_conflicts(issues_by_chunk)
    (out_dir / "book_review.md").write_text(
        merge_book_report(work, reports, conflicts, issues_by_chunk), encoding="utf-8"
    )
    (out_dir / "conflicts.json").write_text(
        json.dumps(conflicts, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"book review -> {out_dir / 'book_review.md'} ({len(conflicts)} conflict(s))")


if __name__ == "__main__":
    main()
