#!/usr/bin/env python3
"""Run the v2 preview migration and end-to-end A+B pipeline.

Usage:
    .venv/bin/python tools/preview_pipeline.py works/<work-dir>
    .venv/bin/python tools/preview_pipeline.py works/<work-dir> --chunks 1 2
    .venv/bin/python tools/preview_pipeline.py works/<work-dir> --migrate-only

The legacy source chunks are re-segmented into chapter/segment state. Legacy
translations are reused by source hash (polished before draft); unmatched segments
are retranslated through the batch protocol, then unpolished targets are polished.
The result is written as translation/preview/book.md and book.html.

``--chunks`` accepts numbers or chunk ids (``1``, ``0001``, ``chunk0001``). Without
it, every legacy chunk that already has a draft or polished translation is used.
``--source`` overrides the preview source paths while keeping the legacy
translations as the reuse index; a changed paragraph therefore stays pending.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any

import batch_translate
import preview_migration


class LocalModelClient:
    """OpenAI-compatible client adapter for the local provider."""

    def __init__(self, model: str, effort: str, max_tokens: int) -> None:
        from model_api import SYSTEM, load_config

        self.base_url, self.api_key = load_config()
        self.model = model
        self.effort = effort
        self.max_tokens = max_tokens
        self.system = SYSTEM

    def __call__(self, prompt: str) -> str:
        from model_api import call

        text, _ = call(
            self.base_url,
            self.api_key,
            self.model,
            f"{self.system}\n\n{prompt}",
            self.effort,
            self.max_tokens,
        )
        return text


def _chunk_values(values: list[str] | None) -> list[str] | None:
    if values is None:
        return None
    chunks: list[str] = []
    for value in values:
        chunks.extend(part for part in value.split(",") if part.strip())
    return chunks


def load_glossary_terms(work_dir: Path) -> list[dict[str, Any]]:
    """Load glossary terms for preview prompt injection."""
    path = Path(work_dir) / "work_temp" / "glossary.json"
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    terms = data.get("terms") if isinstance(data, dict) else None
    return [term for term in terms or [] if isinstance(term, dict)]


def _normalize_chunk_id(value: str) -> str:
    match = re.search(r"(\d+)$", str(value))
    return f"chunk{int(match.group(1)):04d}" if match else str(value)


def _excerpt_head(text: str, limit: int = 600) -> str:
    clean = text.strip()
    return clean[:limit].strip()


def _excerpt_tail(text: str, limit: int = 600) -> str:
    clean = text.strip()
    return clean[-limit:].strip()


def build_neighbour_context(work_dir: Path, chunk_ids: list[str] | None = None) -> str:
    """Build read-only excerpts from chunks immediately around the selection."""
    work = Path(work_dir)
    ids = [
        _normalize_chunk_id(value)
        for value in (chunk_ids or preview_migration.discover_chunk_ids(work))
    ]
    if not ids:
        return ""
    paths = sorted((work / "work_temp").glob("chunk*.md"))
    selected = [position for position, path in enumerate(paths) if path.stem in set(ids)]
    if not selected:
        return ""
    snippets: list[str] = []
    first, last = min(selected), max(selected)
    if first > 0:
        before = paths[first - 1]
        snippets.append(
            f"Previous chunk {before.stem} excerpt:\n{_excerpt_tail(before.read_text(encoding='utf-8'))}"
        )
    if last + 1 < len(paths):
        after = paths[last + 1]
        snippets.append(
            f"Next chunk {after.stem} excerpt:\n{_excerpt_head(after.read_text(encoding='utf-8'))}"
        )
    return "\n\n".join(snippets)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("work_dir", type=Path)
    parser.add_argument("--chunks", nargs="+", help="legacy chunk numbers or ids")
    parser.add_argument("--source", nargs="+", type=Path, help="preview source paths")
    parser.add_argument("--book-dir", type=Path, help="where segment state is written")
    parser.add_argument("--output-dir", type=Path, help="where preview files are written")
    parser.add_argument("--no-polish", action="store_true")
    parser.add_argument("--migrate-only", action="store_true")
    parser.add_argument(
        "--model",
        default=os.environ.get("MALFOID_MODEL", "DeepSeek-V4.1-Flash-1017-TensorFold-Zig"),
    )
    parser.add_argument("--effort", default="low")
    parser.add_argument("--max-tokens", type=int, default=24000)
    parser.add_argument("--budget", type=int, default=batch_translate.DEFAULT_BATCH_BUDGET)
    args = parser.parse_args(argv)

    chunks = _chunk_values(args.chunks)
    if args.migrate_only:
        migration = preview_migration.migrate_preview(
            args.work_dir,
            chunks,
            book_dir=args.book_dir,
            source_paths=args.source,
        )
        render = preview_migration.write_preview(migration.book_dir, output_dir=args.output_dir)
        print(json.dumps(migration.as_dict(), ensure_ascii=False, indent=2))
        print(f"markdown: {render.markdown}")
        print(f"html: {render.html}")
        return

    client = LocalModelClient(args.model, args.effort, args.max_tokens)
    glossary_terms = load_glossary_terms(args.work_dir)
    context = build_neighbour_context(args.work_dir, chunks)
    result = preview_migration.run_preview(
        args.work_dir,
        chunks,
        book_dir=args.book_dir,
        source_paths=args.source,
        client=client,
        polish=not args.no_polish,
        output_dir=args.output_dir,
        budget=args.budget,
        glossary_terms=glossary_terms,
        context=context,
    )
    summary: dict[str, Any] = {
        "migration": result.migration.as_dict(),
        "translation": result.translation.as_dict() if result.translation else None,
        "polish": result.polish.as_dict() if result.polish else None,
        "markdown": str(result.markdown),
        "html": str(result.html),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
