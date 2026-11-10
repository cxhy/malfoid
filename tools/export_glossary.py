#!/usr/bin/env python3
"""Export the machine glossary as a human-readable Markdown table.

Usage:
    .venv/bin/python tools/export_glossary.py works/<work-dir> [more work dirs]

Reads the effective glossary for a v2 series book (global + series + book) and
writes <work>/glossary.md. For legacy flat works it falls back to
<work>/work_temp/glossary.json. The JSON stays the source of truth for
translation; the Markdown is the reader/reviewer-facing table.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import book_state

CATEGORY_LABEL = {
    "person": "人物",
    "place": "地点",
    "object": "物品",
    "creature": "生物",
    "concept": "概念",
}
CATEGORY_ORDER = ["person", "place", "creature", "object", "concept"]


def export(work: Path) -> Path:
    glossary_path = work / "work_temp" / "glossary.json"
    if book_state.is_series_root(work.parent.parent):
        terms = book_state.load_glossary_terms(work)
    else:
        if not glossary_path.is_file():
            raise FileNotFoundError(f"missing glossary: {glossary_path}")
        terms = json.loads(glossary_path.read_text(encoding="utf-8"))["terms"]

    groups: dict[str, list[dict]] = {}
    for t in terms:
        groups.setdefault(t.get("category", "concept"), []).append(t)

    lines = [
        "# 术语表（翻译表）",
        "",
        f"- 作品：`{work.name}`",
        f"- 条目：{len(terms)}",
        "- 机器可读版本：`work_temp/glossary.json`（翻译时逐块注入）",
        "",
    ]
    order = [c for c in CATEGORY_ORDER if c in groups]
    order += [c for c in sorted(groups) if c not in order]
    for cat in order:
        lines += [
            f"## {CATEGORY_LABEL.get(cat, cat)}",
            "",
            "| 原文 | 译文 | 别名 | 性别 | 频次 | 说明 |",
            "|---|---|---|---|---|---|",
        ]
        rows = sorted(groups[cat], key=lambda t: (-t.get("frequency", 0), t["source"]))
        for t in rows:
            aliases = "、".join(t.get("aliases") or [])
            notes = (t.get("notes") or "").replace("|", "／").replace("\n", " ")
            lines.append(
                f"| {t['source']} | {t['target']} | {aliases} | "
                f"{t.get('gender', 'unknown')} | {t.get('frequency', 0)} | {notes} |"
            )
        lines.append("")

    dest = work / "glossary.md"
    dest.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return dest


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("works", type=Path, nargs="+")
    args = ap.parse_args()
    for work in args.works:
        print(f"{work.name}: {export(work)}")


if __name__ == "__main__":
    main()
