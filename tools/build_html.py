#!/usr/bin/env python3
"""Build the polished HTML with the translate-book web template.

Usage:
    .venv/bin/python tools/build_html.py works/<work-dir> [more work dirs]

Reads <work>/translation/polished/book.md, converts it with Pandoc, wraps the
body in .pi/skills/translate-book/scripts/template.html (the same template
merge_and_build.py uses for the draft book.html), and writes
<work>/translation/polished/book.html.

Run tools/finalize.py afterwards to copy it to translation/<Original_Title>.html.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / ".pi/skills/translate-book/scripts/template.html"
ZH_FONT = "'FangSong', '仿宋', 'STFangSong', '华文仿宋', serif"
ZH_TOC = "目录"


def pandoc_body(md: Path, title: str) -> str:
    with tempfile.NamedTemporaryFile(suffix=".html", delete=False) as tmp:
        out = Path(tmp.name)
    subprocess.run(
        [
            "pandoc",
            str(md),
            "-o",
            str(out),
            "--standalone",
            "--metadata",
            f"title={title}",
            "--metadata",
            "lang=zh-CN",
            "--from",
            "markdown+smart+east_asian_line_breaks",
            "--to",
            "html5",
            "--mathml",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    html = out.read_text(encoding="utf-8")
    out.unlink(missing_ok=True)
    m = re.search(r"<body[^>]*>(.*?)</body>", html, re.DOTALL | re.IGNORECASE)
    body = m.group(1).strip() if m else html
    body = re.sub(r"<hr\s*/?>", '<div class="page-separator"></div>', body)
    body = re.sub(r"<p>\s*---\s*</p>", '<div class="page-separator"></div>', body)
    return body


def build_from(work: Path, md: Path, dest: Path, title: str) -> Path:
    """Render one markdown file into the web template at `dest`."""
    meta = json.loads((work / "meta.json").read_text(encoding="utf-8"))
    author = meta.get("author") or ""
    if not md.is_file():
        raise FileNotFoundError(f"missing markdown: {md}")

    template = TEMPLATE.read_text(encoding="utf-8")
    full = template.replace("$body$", pandoc_body(md, title))
    full = full.replace("$title$", title)
    full = full.replace("$lang$", "zh-CN")
    full = full.replace("$body_font$", ZH_FONT)
    full = full.replace("$toc_label$", ZH_TOC)
    if author:
        full = re.sub(
            r"(<head[^>]*>)",
            rf'\1\n    <meta name="author" content="{author}">',
            full,
            count=1,
            flags=re.IGNORECASE,
        )

    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(full, encoding="utf-8")
    return dest


def build(work: Path) -> Path:
    meta = json.loads((work / "meta.json").read_text(encoding="utf-8"))
    title = meta.get("title") or work.name
    return build_from(
        work, work / "translation/polished/book.md", work / "translation/polished/book.html", title
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("works", type=Path, nargs="+")
    args = ap.parse_args()
    for work in args.works:
        print(f"{work.name}: {build(work)}")


if __name__ == "__main__":
    main()
