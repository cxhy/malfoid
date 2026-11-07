#!/usr/bin/env python3
"""Generic v2 book review runner.

Review blocks are planned over the whole book as one contiguous sequence. Each
segment receives an unambiguous global review index; issues are mapped back to
their chapter and chapter-local segment position before evidence verification and
report writing. Review stays advisory and writes only under ``translation/review``.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any

import review_blocks
import review_evidence
import review_reports
import segment_state
from profile_rules import section

REVIEW_SYSTEM = (
    "You are a meticulous bilingual literary reviewer. Compare each numbered "
    "English source segment with its Chinese target segment. Report only issues "
    "supported by the supplied text, and follow the requested JSON protocol "
    "exactly. Never rewrite or translate the text."
)


def load_chapters(book_dir: Path) -> list[dict[str, Any]]:
    """Load chapter state in numeric order."""
    chapters_dir = Path(book_dir) / "source" / segment_state.CHAPTERS_DIR

    def sort_key(path: Path) -> int:
        match = re.search(r"(\d+)$", path.stem)
        return int(match.group(1)) if match else 0

    paths = sorted(chapters_dir.glob("ch-*.json"), key=sort_key)
    return [segment_state.load_chapter(path) for path in paths]


def flatten_segments(
    chapters: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[int, tuple[int, int]]]:
    """Flatten chapters into one contiguous review sequence.

    Each segment gets a global ``index`` for the review prompt and keeps its
    chapter and chapter-local ``local_index``. The returned map resolves a global
    index back to ``(chapter, local_index)``.
    """
    segments: list[dict[str, Any]] = []
    global_map: dict[int, tuple[int, int]] = {}
    for position, chapter in enumerate(chapters):
        number = int(chapter.get("chapter") or position + 1)
        for segment in chapter.get("segments") or []:
            local_index = int(segment.get("index", 0))
            item = dict(segment)
            item["index"] = len(segments)
            item["chapter"] = number
            item["local_index"] = local_index
            global_map[item["index"]] = (number, local_index)
            segments.append(item)
    return segments, global_map


def map_reviews(
    reviews: list[dict[str, Any]], global_map: dict[int, tuple[int, int]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Map global issue indices to chapter/local positions."""
    mapped: list[dict[str, Any]] = []
    untraceable: list[dict[str, Any]] = []
    for review in reviews:
        copy = dict(review)
        issues: list[dict[str, Any]] = []
        for issue in review.get("issues") or []:
            try:
                global_index = int(issue.get("segment_index"))
            except (TypeError, ValueError):
                global_index = -1
            location = global_map.get(global_index)
            if location is None:
                untraceable.append(
                    {
                        "global_segment_index": global_index,
                        "message": issue.get("message") or "",
                        "reason": "issue references an unknown review segment",
                    }
                )
                continue
            chapter, local_index = location
            item = dict(issue)
            item["segment_index"] = local_index
            item["chapter"] = chapter
            item["global_segment_index"] = global_index
            issues.append(item)
        copy["issues"] = issues
        mapped.append(copy)
    return mapped, untraceable


def map_candidates(
    candidates: list[dict[str, Any]], global_map: dict[int, tuple[int, int]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Map global candidate indices to chapter/local positions and refresh ids."""
    mapped: list[dict[str, Any]] = []
    untraceable: list[dict[str, Any]] = []
    for candidate in candidates:
        try:
            global_index = int(candidate.get("segment_index"))
        except (TypeError, ValueError):
            global_index = -1
        location = global_map.get(global_index)
        if location is None:
            untraceable.append(
                {
                    "global_segment_index": global_index,
                    "message": candidate.get("message") or "",
                    "reason": "candidate references an unknown review segment",
                }
            )
            continue
        chapter, local_index = location
        item = dict(candidate)
        item["segment_index"] = local_index
        item["chapter"] = chapter
        item["global_segment_index"] = global_index
        item["id"] = review_evidence.candidate_id(item)
        mapped.append(item)
    return mapped, untraceable


def verify_and_arbitrate(
    client: Any,
    candidates: list[dict[str, Any]],
    chapters: list[dict[str, Any]],
    *,
    max_rounds: int = review_evidence.DEFAULT_EVIDENCE_ROUNDS,
    max_refs: int = review_evidence.DEFAULT_MAX_EVIDENCE_REFS,
    max_chars: int = review_evidence.DEFAULT_MAX_EVIDENCE_CHARS,
    verified_only: bool = False,
) -> dict[str, Any]:
    """Verify candidates, group conflicts and arbitrate them."""
    verified = review_evidence.verify_candidates(
        client,
        candidates,
        chapters,
        max_rounds=max_rounds,
        max_refs=max_refs,
        max_chars=max_chars,
    )
    conflicts = review_evidence.group_conflicts(verified, verified_only=verified_only)
    arbitrations = review_evidence.arbitrate(client, conflicts)
    return {
        "candidates": verified,
        "conflicts": arbitrations,
        "unresolved_conflicts": [result for result in arbitrations if result["needs_human"]],
        "confirmed": [record for record in verified if record["status"] == "confirmed"],
        "rejected": [record for record in verified if record["status"] == "rejected"],
        "unresolved_candidates": [
            record for record in verified if record["status"] == "unresolved"
        ],
    }


def review_book(
    book_dir: Path,
    client: Any,
    *,
    budget: int = review_blocks.DEFAULT_REVIEW_BUDGET,
    workers: int = review_blocks.DEFAULT_WORKERS,
    max_bisect_depth: int = review_blocks.DEFAULT_BISECT_DEPTH,
    max_segment_retries: int = review_blocks.DEFAULT_SEGMENT_RETRIES,
    max_calls: int | None = review_blocks.DEFAULT_MAX_CALLS,
    limit_blocks: int | None = None,
    measure=None,
    cache_dir: Path | None = None,
    trace_dir: Path | None = None,
    report_dir: Path | None = None,
) -> dict[str, Any]:
    """Review a whole book with one global block plan and write reports."""
    book = Path(book_dir)
    chapters = load_chapters(book)
    if not chapters:
        raise ValueError(f"book has no chapter state: {book}")
    segments, global_map = flatten_segments(chapters)
    blocks = review_blocks.plan_blocks(segments, budget, measure)
    if limit_blocks is not None:
        if limit_blocks < 1:
            raise ValueError("limit_blocks must be positive")
        blocks = blocks[:limit_blocks]
    rules = "\n\n".join(
        part
        for part in (
            section("proofread", work_dir=book),
            section("book_review", work_dir=book),
        )
        if isinstance(part, str) and part.strip()
    )
    reviews = review_blocks.run_review(
        client,
        segments,
        budget=budget,
        workers=workers,
        max_bisect_depth=max_bisect_depth,
        max_segment_retries=max_segment_retries,
        max_calls=max_calls,
        limit_blocks=limit_blocks,
        measure=measure,
        cache_dir=cache_dir,
        trace_dir=trace_dir,
        rules=rules,
    )
    if len(reviews) != len(blocks):
        raise RuntimeError("review block planning changed between plan and run")
    candidates = review_evidence.collect_candidates(reviews, blocks)
    candidates, untraceable_candidates = map_candidates(candidates, global_map)
    local_reviews, untraceable_issues = map_reviews(reviews, global_map)
    evidence = verify_and_arbitrate(client, candidates, chapters)
    summary = review_reports.write_review_reports(
        book, local_reviews, evidence_result=evidence, report_dir=report_dir
    )
    return {
        "book_dir": str(book),
        "advisory": True,
        "segments": segments,
        "blocks": blocks,
        "reviews": local_reviews,
        "evidence": evidence,
        "summary": summary,
        "untraceable_issues": untraceable_issues + untraceable_candidates,
    }


class ReviewModelClient:
    """OpenAI-compatible client adapter for the local review provider."""

    def __init__(
        self,
        model: str,
        effort: str,
        max_tokens: int,
        usage_log: Path | None = None,
    ) -> None:
        from model_api import load_config

        self.base_url, self.api_key = load_config()
        self.model = model
        self.effort = effort
        self.max_tokens = max_tokens
        self.usage_log = usage_log
        self._lock = threading.Lock()

    def _record(self, usage: dict[str, Any], prompt: str, elapsed: float) -> None:
        if self.usage_log is None:
            return
        row = {
            "ts": time.time(),
            "model": self.model,
            "effort": self.effort,
            "max_tokens": self.max_tokens,
            "prompt_chars": len(prompt),
            "duration": round(elapsed, 2),
            "usage": usage,
        }
        with self._lock:
            self.usage_log.parent.mkdir(parents=True, exist_ok=True)
            with self.usage_log.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    def __call__(self, prompt: str) -> str:
        started = time.time()
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": REVIEW_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.2,
            "max_tokens": self.max_tokens,
            "reasoning_effort": self.effort,
            "response_format": {"type": "json_object"},
        }
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(body).encode(),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
        )
        timeout = int(os.environ.get("MALFOID_REVIEW_TIMEOUT", "900"))
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read())
        usage = data.get("usage") or {}
        usage["finish_reason"] = data["choices"][0].get("finish_reason")
        self._record(usage, prompt, time.time() - started)
        return data["choices"][0]["message"]["content"]


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("book_dir", type=Path, help="book sub-work directory")
    parser.add_argument(
        "--model",
        default=os.environ.get("MALFOID_MODEL", "DeepSeek-V4.1-Flash-1017-TensorFold-Zig"),
    )
    parser.add_argument("--effort", default="none")
    parser.add_argument("--max-tokens", type=int, default=24000)
    parser.add_argument("--budget", type=int, default=review_blocks.DEFAULT_REVIEW_BUDGET)
    parser.add_argument("--workers", type=int, default=review_blocks.DEFAULT_WORKERS)
    parser.add_argument("--max-bisect-depth", type=int, default=review_blocks.DEFAULT_BISECT_DEPTH)
    parser.add_argument(
        "--max-segment-retries", type=int, default=review_blocks.DEFAULT_SEGMENT_RETRIES
    )
    parser.add_argument("--max-calls", type=int, default=review_blocks.DEFAULT_MAX_CALLS)
    parser.add_argument(
        "--limit-blocks",
        type=int,
        help="review only the first N blocks (prompt/effort smoke test)",
    )
    parser.add_argument("--cache-dir", type=Path, help="review block cache directory")
    parser.add_argument("--trace-dir", type=Path, help="review recovery trace directory")
    parser.add_argument(
        "--report-dir",
        type=Path,
        help="write reports here instead of translation/review (smoke tests)",
    )
    parser.add_argument("--no-cache", action="store_true")
    args = parser.parse_args(argv)

    cache_dir = None
    if not args.no_cache:
        cache_dir = args.cache_dir or args.book_dir / "work_temp" / "review_cache"
    trace_dir = args.trace_dir or args.book_dir / "work_temp" / "review_traces"
    report_dir = args.report_dir
    if report_dir is None and args.limit_blocks:
        report_dir = args.book_dir / "translation" / "review" / "smoke"
    client = ReviewModelClient(
        args.model,
        args.effort,
        args.max_tokens,
        usage_log=args.book_dir / "work_temp" / "usage.jsonl",
    )
    result = review_book(
        args.book_dir,
        client,
        budget=args.budget,
        workers=args.workers,
        max_bisect_depth=args.max_bisect_depth,
        max_segment_retries=args.max_segment_retries,
        max_calls=args.max_calls,
        limit_blocks=args.limit_blocks,
        cache_dir=cache_dir,
        trace_dir=trace_dir,
        report_dir=report_dir,
    )
    print(
        json.dumps(
            {
                "summary": result["summary"],
                "untraceable_issues": result["untraceable_issues"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
