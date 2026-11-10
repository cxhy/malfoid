#!/usr/bin/env python3
"""Generic v2 book delivery.

Render translated segment state to ``translation/<title>.md`` and then build the
reader-facing HTML through ``build_html.build_from`` and the bundled web template.
The delivery layer never edits segment state; it only reads it and writes files.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import build_html
import segment_state
import verify_book


@dataclass(frozen=True)
class DeliveryResult:
    """Paths written by one book delivery run."""

    title: str
    markdown: Path
    html: Path
    markdown_parts: list[Path]


def _read_meta(book_dir: Path) -> dict[str, Any]:
    path = Path(book_dir) / "meta.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _ensure_meta(book_dir: Path, title: str, author: str) -> None:
    meta = _read_meta(book_dir)
    meta["title"] = title
    meta["author"] = author
    path = Path(book_dir) / "meta.json"
    path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def safe_title(title: str) -> str:
    """Make a title usable as a delivery filename on common platforms."""
    cleaned = re.sub(r"[/\\:*?\"<>|]+", "-", title).strip().strip(".")
    return cleaned or "book"


def delivery_paths(book_dir: Path, title: str) -> tuple[Path, Path]:
    """Return the Markdown and HTML delivery paths for a title."""
    output_dir = Path(book_dir) / "translation"
    name = safe_title(title)
    return output_dir / f"{name}.md", output_dir / f"{name}.html"


def render_markdown(book_dir: Path) -> str:
    """Render translated segment state to Markdown."""
    return segment_state.render_book(Path(book_dir), translated=True)


def _chapter_blocks(markdown: str) -> list[str]:
    blocks = [block for block in re.split(r"(?m)^(?=# )", markdown) if block.strip()]
    return blocks or [markdown]


def _minimum_parts(sizes: list[int], limit: int) -> int:
    parts = 1
    current = 0
    for size in sizes:
        if current and current + size > limit:
            parts += 1
            current = size
        else:
            current += size
    return parts


def _balanced_partition(sizes: list[int], limit: int) -> list[tuple[int, int]]:
    """Split chapter sizes into the fewest parts, balanced by squared deviation."""
    if any(size > limit for size in sizes):
        raise ValueError("a chapter block exceeds the Markdown part limit")
    total = sum(sizes)
    if total <= limit:
        return []
    parts = _minimum_parts(sizes, limit)
    target = total / parts
    count = len(sizes)
    prefix = [0]
    for size in sizes:
        prefix.append(prefix[-1] + size)
    infinity = float("inf")
    cost = [[infinity] * (count + 1) for _ in range(parts + 1)]
    previous = [[-1] * (count + 1) for _ in range(parts + 1)]
    cost[0][0] = 0.0
    for part in range(1, parts + 1):
        for end in range(1, count + 1):
            for start in range(part - 1, end):
                block_size = prefix[end] - prefix[start]
                if block_size > limit or cost[part - 1][start] == infinity:
                    continue
                candidate = cost[part - 1][start] + (block_size - target) ** 2
                if candidate < cost[part][end]:
                    cost[part][end] = candidate
                    previous[part][end] = start
    if cost[parts][count] == infinity:
        raise ValueError("cannot partition Markdown into parts under the limit")
    boundaries = [count]
    end = count
    for part in range(parts, 0, -1):
        start = previous[part][end]
        if start < 0:
            raise ValueError("cannot reconstruct Markdown part boundaries")
        boundaries.append(start)
        end = start
    boundaries.reverse()
    return list(zip(boundaries[:-1], boundaries[1:]))


def split_markdown(markdown: str, limit: int) -> list[str]:
    """Split Markdown at chapter boundaries into balanced parts under ``limit``."""
    if limit <= 0:
        raise ValueError("Markdown part limit must be positive")
    blocks = _chapter_blocks(markdown)
    sizes = [len(block) for block in blocks]
    ranges = _balanced_partition(sizes, limit)
    return [
        "\n\n".join(block.strip("\n") for block in blocks[start:end]).rstrip() + "\n"
        for start, end in ranges
    ]


def part_paths(book_dir: Path, title: str, count: int) -> list[Path]:
    """Return the Markdown part paths for a title."""
    output_dir = Path(book_dir) / "translation"
    name = safe_title(title)
    return [output_dir / f"{name} - Part {index}.md" for index in range(1, count + 1)]


def write_delivery(
    book_dir: Path,
    *,
    title: str | None = None,
    author: str | None = None,
    split_chars: int | None = 80000,
    verify: bool = True,
) -> DeliveryResult:
    """Render Markdown and template HTML under ``translation/<title>``.

    The full Markdown and single HTML are always kept. When ``split_chars`` is
    given and the Markdown exceeds it, balanced chapter-boundary parts are also
    written as ``<title> - Part N.md``. A single chapter larger than the limit
    is an error: parts never split inside a chapter.
    """
    book = Path(book_dir)
    if verify:
        report = verify_book.verify_book(book)
        if not report["ok"]:
            raise verify_book.DeliveryVerificationError(report)
    meta = _read_meta(book)
    resolved_title = title or str(meta.get("title") or book.name)
    resolved_author = author if author is not None else str(meta.get("author") or "")
    _ensure_meta(book, resolved_title, resolved_author)

    markdown_path, html_path = delivery_paths(book, resolved_title)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown = render_markdown(book)
    markdown_path.write_text(markdown, encoding="utf-8")
    build_html.build_from(book, markdown_path, html_path, resolved_title)

    parts: list[Path] = []
    output_dir = markdown_path.parent
    name = safe_title(resolved_title)
    for stale in output_dir.glob(f"{name} - Part *.md"):
        stale.unlink()
    if split_chars is not None:
        texts = split_markdown(markdown, split_chars)
        parts = part_paths(book, resolved_title, len(texts))
        for path, text in zip(parts, texts):
            path.write_text(text, encoding="utf-8")
    return DeliveryResult(
        title=resolved_title,
        markdown=markdown_path,
        html=html_path,
        markdown_parts=parts,
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("book_dir", type=Path)
    parser.add_argument("--title")
    parser.add_argument("--author")
    parser.add_argument("--split-md-chars", type=int, default=80000)
    parser.add_argument("--no-split-md", action="store_true")
    parser.add_argument("--skip-verify", action="store_true")
    args = parser.parse_args(argv)
    split_chars = None if args.no_split_md else args.split_md_chars
    try:
        result = write_delivery(
            args.book_dir,
            title=args.title,
            author=args.author,
            split_chars=split_chars,
            verify=not args.skip_verify,
        )
    except verify_book.DeliveryVerificationError as exc:
        print(json.dumps(exc.report, ensure_ascii=False, indent=2))
        raise SystemExit(1)
    print(f"markdown: {result.markdown}")
    print(f"html: {result.html}")
    for path in result.markdown_parts:
        print(f"part: {path}")


if __name__ == "__main__":
    main()
