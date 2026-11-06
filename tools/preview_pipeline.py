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
from pathlib import Path
from typing import Any

import batch_translate
import preview_migration


class LocalModelClient:
    """OpenAI-compatible client adapter for the local provider."""

    def __init__(self, model: str, effort: str, max_tokens: int) -> None:
        from translate_chunk import SYSTEM, load_config

        self.base_url, self.api_key = load_config()
        self.model = model
        self.effort = effort
        self.max_tokens = max_tokens
        self.system = SYSTEM

    def __call__(self, prompt: str) -> str:
        from translate_chunk import call

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
    result = preview_migration.run_preview(
        args.work_dir,
        chunks,
        book_dir=args.book_dir,
        source_paths=args.source,
        client=client,
        polish=not args.no_polish,
        output_dir=args.output_dir,
        budget=args.budget,
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
