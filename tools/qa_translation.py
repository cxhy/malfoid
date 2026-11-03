#!/usr/bin/env python3
"""QA checks for translated chunks produced by the translate-book pipeline.

Usage:
    .venv/bin/python tools/qa_translation.py works/<work>/work_temp [chunk0001 ...]

Checks per chunk:
  - output exists and is non-empty
  - heading count preserved
  - paragraph/block count close to source
  - length ratio (Chinese chars / English words) within a sane band
  - English residue outside markdown URLs/headings
  - glossary terms present in source are translated with the canonical target
Exit code 1 when any check fails.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

LATIN = re.compile(r"[A-Za-z]")
CJK = re.compile(r"[\u4e00-\u9fff]")
URLISH = re.compile(r"(!?\[[^\]]*\]\([^)]*\)|<[^>]+>|https?://\S+)")
# Family mottos kept in their original Latin/French with a Chinese gloss after them.
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
        words = re.findall(r"[A-Za-z][A-Za-z'’-]{2,}", line)
        if words:
            hits.append(line.strip()[:160])
    return hits


def main() -> int:
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    temp = Path(sys.argv[1])
    chunk_ids = sys.argv[2:] or sorted(
        p.stem for p in temp.glob("chunk*.md") if not p.name.startswith("output_")
    )
    glossary = json.loads((temp / "glossary.json").read_text(encoding="utf-8"))
    failures = 0
    for cid in chunk_ids:
        src_path = temp / f"{cid}.md"
        out_path = temp / f"output_{cid}.md"
        print(f"== {cid}")
        if not out_path.is_file() or not out_path.read_text(encoding="utf-8").strip():
            print("  FAIL output missing or empty")
            failures += 1
            continue
        src = src_path.read_text(encoding="utf-8")
        out = out_path.read_text(encoding="utf-8")

        hs, ho = headings(src), headings(out)
        if hs != ho:
            print(f"  FAIL headings {hs} -> {ho}")
            failures += 1

        bs, bo = blocks(src), blocks(out)
        if abs(bs - bo) > max(1, bs * 0.15):
            print(f"  WARN blocks {bs} -> {bo}")

        en_words = len(re.findall(r"[A-Za-z']+", src))
        zh_chars = len(CJK.findall(out)) if False else len(CJK.findall(out))
        ratio = zh_chars / max(1, en_words)
        if not (1.0 <= ratio <= 2.6):
            print(f"  FAIL length ratio {ratio:.2f} (zh chars {zh_chars} / en words {en_words})")
            failures += 1
        else:
            print(f"  ok length ratio {ratio:.2f}")

        residue = english_residue(out)
        if residue:
            print(f"  FAIL english residue in {len(residue)} line(s)")
            for line in residue[:5]:
                print(f"      {line}")
            failures += 1

        def covered(form: str) -> bool:
            """True when every occurrence of `form` sits inside a longer glossary term."""
            hits = [m.start() for m in re.finditer(rf"\b{re.escape(form)}\b", src)]
            if not hits:
                return True
            spans = []
            for other in glossary.get("terms", []):
                for of in [other["source"], *other.get("aliases", [])]:
                    if len(of) <= len(form) or form.lower() not in of.lower():
                        continue
                    for m in re.finditer(rf"\b{re.escape(of)}\b", src):
                        spans.append((m.start(), m.end()))
            return all(any(a <= h < b for a, b in spans) for h in hits)

        missing = []
        for term in glossary.get("terms", []):
            forms = [term["source"], *term.get("aliases", [])]
            hit = False
            for f in forms:
                if re.search(rf"\b{re.escape(f)}\b", src) and not covered(f):
                    hit = True
                    break
            if hit:
                target = term["target"]
                heads = {target, *[part for part in target.split("·") if part]}
                if not any(h and h in out for h in heads):
                    missing.append(f"{term['source']}->{target}")
        if missing:
            print(f"  FAIL missing terms: {', '.join(missing)}")
            failures += 1
        else:
            print("  ok terms")

    print(f"\n{'FAIL' if failures else 'PASS'}: {failures} failing check(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
