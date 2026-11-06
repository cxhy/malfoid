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


@dataclass(frozen=True)
class DeliveryResult:
    """Paths written by one book delivery run."""

    title: str
    markdown: Path
    html: Path


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


def write_delivery(
    book_dir: Path,
    *,
    title: str | None = None,
    author: str | None = None,
) -> DeliveryResult:
    """Render Markdown and template HTML under ``translation/<title>``."""
    book = Path(book_dir)
    meta = _read_meta(book)
    resolved_title = title or str(meta.get("title") or book.name)
    resolved_author = author if author is not None else str(meta.get("author") or "")
    _ensure_meta(book, resolved_title, resolved_author)

    markdown_path, html_path = delivery_paths(book, resolved_title)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text(render_markdown(book), encoding="utf-8")
    build_html.build_from(book, markdown_path, html_path, resolved_title)
    return DeliveryResult(title=resolved_title, markdown=markdown_path, html=html_path)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("book_dir", type=Path)
    parser.add_argument("--title")
    parser.add_argument("--author")
    args = parser.parse_args(argv)
    result = write_delivery(args.book_dir, title=args.title, author=args.author)
    print(f"markdown: {result.markdown}")
    print(f"html: {result.html}")


if __name__ == "__main__":
    main()
