"""Review block planning, structured validation and bisection tests (ticket 08)."""

from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path

import batch_translate
import pytest
import review_blocks
import segment_state

_INDEX_RE = re.compile(r"^\[(\d+)\]$", re.MULTILINE)


def _segment(index: int, source: str, target: str | None = None) -> dict:
    return {
        "index": index,
        "kind": "text",
        "source": source,
        "source_hash": f"hash-{index}",
        "target": target,
    }


def _payload(indices: list[int], *, complete: bool = True, issues: list | None = None) -> str:
    return json.dumps(
        {
            "issues": issues or [],
            "reviewed_segments": len(indices),
            "complete": complete,
        },
        ensure_ascii=False,
    )


def _prompt_indices(prompt: str) -> list[int]:
    return [int(value) for value in _INDEX_RE.findall(prompt)]


class ReviewClient:
    """Offline model boundary that answers from the prompt's segment indices."""

    def __init__(self, responder=None) -> None:
        self.responder = responder
        self.calls: list[str] = []
        self.active = 0
        self.max_active = 0
        self._lock = threading.Lock()

    def __call__(self, prompt: str) -> str:
        with self._lock:
            self.calls.append(prompt)
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            indices = _prompt_indices(prompt)
            if self.responder is not None:
                return self.responder(indices)
            return _payload(
                indices,
                issues=[
                    {
                        "segment_index": indices[0],
                        "message": "称呼漂移：同一人物在不同段落使用了不同称呼。",
                    }
                ],
            )
        finally:
            with self._lock:
                self.active -= 1

    def call_count(self) -> int:
        return len(self.calls)


class SequenceClient:
    """Offline fake returning canned responses in call order."""

    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.calls: list[str] = []
        self._lock = threading.Lock()

    def __call__(self, prompt: str) -> str:
        with self._lock:
            self.calls.append(prompt)
            if not self.responses:
                raise AssertionError("unexpected model call")
            return self.responses.pop(0)


class ObjectClient:
    def __init__(self, response: str) -> None:
        self.response = response
        self.prompts: list[str] = []

    def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.response


def test_plan_blocks_packs_contiguous_segments_within_the_budget() -> None:
    segments = [_segment(i, "x" * 4) for i in range(5)]
    blocks = review_blocks.plan_blocks(segments, 8, measure=len)
    assert [[segment["index"] for segment in block] for block in blocks] == [
        [0, 1],
        [2, 3],
        [4],
    ]
    assert all(sum(len(segment["source"]) for segment in block) <= 8 for block in blocks)


def test_plan_blocks_uses_three_translation_batches_by_default() -> None:
    assert review_blocks.DEFAULT_REVIEW_BUDGET == 3 * batch_translate.DEFAULT_BATCH_BUDGET
    segments = [_segment(i, "x" * 4) for i in range(5)]
    blocks = review_blocks.plan_blocks(segments, measure=len)
    assert [[segment["index"] for segment in block] for block in blocks] == [[0, 1, 2, 3, 4]]


def test_plan_blocks_is_deterministic() -> None:
    segments = [_segment(i, "x" * size) for i, size in enumerate([5, 7, 2, 9, 1])]
    first = review_blocks.plan_blocks(segments, 10, measure=len)
    second = review_blocks.plan_blocks(segments, 10, measure=len)
    assert [[segment["index"] for segment in block] for block in first] == [
        [0],
        [1, 2],
        [3, 4],
    ]
    assert first == second


def test_plan_blocks_rejects_an_oversized_segment() -> None:
    with pytest.raises(ValueError, match="exceeds"):
        review_blocks.plan_blocks([_segment(0, "x" * 9)], 8, measure=len)


def test_plan_blocks_rejects_a_non_positive_budget() -> None:
    with pytest.raises(ValueError, match="positive"):
        review_blocks.plan_blocks([_segment(0, "x")], 0, measure=len)


def test_plan_blocks_handles_no_segments() -> None:
    assert review_blocks.plan_blocks([], 10) == []


def test_validate_review_output_accepts_a_complete_payload() -> None:
    payload = _payload(
        [0, 1, 2],
        issues=[{"segment_index": 1, "message": "术语漂移：称呼不一致。"}],
    )
    result = review_blocks.validate_review_output(payload, 3)
    assert result["reviewed_segments"] == 3
    assert result["complete"] is True
    assert result["issues"] == [{"segment_index": 1, "message": "术语漂移：称呼不一致。"}]


def test_validate_review_output_accepts_a_parsed_payload() -> None:
    payload = {
        "issues": [{"segment_index": 0, "message": "术语漂移。"}],
        "reviewed_segments": 2,
        "complete": True,
    }
    result = review_blocks.validate_review_output(payload, 2)
    assert result["reviewed_segments"] == 2
    assert result["issues"] == payload["issues"]


def test_validate_review_output_rejects_a_wrong_reviewed_count() -> None:
    payload = json.dumps(
        {"issues": [], "reviewed_segments": 2, "complete": True},
        ensure_ascii=False,
    )
    with pytest.raises(review_blocks.ReviewProtocolError, match="reviewed"):
        review_blocks.validate_review_output(payload, 3)


def test_validate_review_output_rejects_missing_completeness() -> None:
    payload = json.dumps({"issues": [], "reviewed_segments": 3}, ensure_ascii=False)
    with pytest.raises(review_blocks.ReviewProtocolError, match="complete"):
        review_blocks.validate_review_output(payload, 3)


def test_validate_review_output_rejects_incomplete_output() -> None:
    payload = _payload([0, 1], complete=False)
    with pytest.raises(review_blocks.ReviewProtocolError, match="complete"):
        review_blocks.validate_review_output(payload, 2)


def test_validate_review_output_rejects_a_malformed_shape() -> None:
    with pytest.raises(review_blocks.ReviewProtocolError):
        review_blocks.validate_review_output(json.dumps([1, 2, 3]), 3)
    with pytest.raises(review_blocks.ReviewProtocolError, match="issues"):
        review_blocks.validate_review_output(
            json.dumps({"issues": "none", "reviewed_segments": 3, "complete": True}),
            3,
        )
    with pytest.raises(review_blocks.ReviewProtocolError, match="segment_index"):
        review_blocks.validate_review_output(
            json.dumps(
                {
                    "issues": [{"message": "missing index"}],
                    "reviewed_segments": 3,
                    "complete": True,
                }
            ),
            3,
        )


def test_run_review_returns_structured_results_in_block_order() -> None:
    segments = [
        _segment(0, "x" * 10, "德拉科等待着。"),
        _segment(1, "y" * 10, "哈利抬起头。"),
        _segment(2, "z" * 10, "他们离开了。"),
        _segment(3, "w" * 10, "火车开动了。"),
    ]
    client = ReviewClient()
    results = review_blocks.run_review(client, segments, budget=20, measure=len, workers=2)
    assert [result["segments"] for result in results] == [[0, 1], [2, 3]]
    assert [result["reviewed_segments"] for result in results] == [2, 2]
    assert [result["complete"] for result in results] == [True, True]
    assert [result["start"] for result in results] == [0, 2]


def test_run_review_builds_a_prompt_with_source_and_target() -> None:
    segments = [_segment(0, "Draco waited.", "德拉科等待着。")]
    client = ReviewClient()
    review_blocks.run_review(client, segments, budget=100, workers=1)
    prompt = client.calls[0]
    assert "TASK: REVIEW" in prompt
    assert "SOURCE: Draco waited." in prompt
    assert "TARGET: 德拉科等待着。" in prompt
    assert "reviewed_segments" in prompt


def test_run_review_accepts_an_object_client() -> None:
    segments = [_segment(0, "One.", "一。")]
    client = ObjectClient(_payload([0]))
    results = review_blocks.run_review(client, segments, budget=100, workers=1)
    assert results[0]["complete"] is True
    assert results[0]["reviewed_segments"] == 1
    assert len(client.prompts) == 1


def test_run_review_sorts_issues_deterministically() -> None:
    segments = [_segment(i, f"Segment {i}.", f"第{i}段。") for i in range(3)]
    client = ReviewClient(
        lambda indices: _payload(
            indices,
            issues=[
                {"segment_index": indices[-1], "message": "后段问题"},
                {"segment_index": indices[0], "message": "前段问题"},
            ],
        )
    )
    first = review_blocks.run_review(client, segments, budget=100, workers=1)
    second_client = ReviewClient(
        lambda indices: _payload(
            indices,
            issues=[
                {"segment_index": indices[0], "message": "前段问题"},
                {"segment_index": indices[-1], "message": "后段问题"},
            ],
        )
    )
    second = review_blocks.run_review(second_client, segments, budget=100, workers=1)
    assert [issue["segment_index"] for issue in first[0]["issues"]] == [0, 2]
    assert first[0]["issues"] == second[0]["issues"]


def test_run_review_bisects_invalid_block_output() -> None:
    segments = [_segment(i, f"Segment {i}.", f"第{i}段。") for i in range(3)]
    client = ReviewClient(
        lambda indices: _payload(indices[:1]) if len(indices) > 1 else _payload(indices)
    )
    results = review_blocks.run_review(client, segments, budget=100, workers=1, max_bisect_depth=2)
    assert len(results) == 1
    result = results[0]
    assert result["segments"] == [0, 1, 2]
    assert result["reviewed_segments"] == 3
    assert result["complete"] is True
    assert result["stage"] == "bisected"
    assert [part["segments"] for part in result["parts"]] == [[0], [1], [2]]
    assert client.call_count() == 5
    assert sum(1 for prompt in client.calls if len(_prompt_indices(prompt)) > 1) == 2


def test_run_review_bounds_bisection_depth_and_falls_back_per_segment() -> None:
    segments = [_segment(i, f"Segment {i}.", f"第{i}段。") for i in range(8)]
    client = ReviewClient(lambda indices: _payload([]))
    results = review_blocks.run_review(
        client,
        segments,
        budget=100,
        workers=1,
        max_bisect_depth=1,
        max_segment_retries=0,
    )
    result = results[0]
    assert result["complete"] is False
    assert result["reviewed_segments"] == 0
    assert len(result["parts"]) == 8
    assert client.call_count() == 9
    assert all(part["complete"] is False for part in result["parts"])


def test_run_review_bounds_per_segment_retries() -> None:
    segments = [_segment(0, "One.", "一。")]
    invalid = _payload([0], complete=False)
    valid = _payload([0])
    client = SequenceClient([invalid, valid])
    results = review_blocks.run_review(
        client, segments, budget=100, workers=1, max_segment_retries=1
    )
    assert results[0]["complete"] is True
    assert results[0]["reviewed_segments"] == 1
    assert results[0]["attempts"] == 2
    assert len(client.calls) == 2


def test_run_review_stops_after_the_segment_retry_limit() -> None:
    segments = [_segment(0, "One.", "一。")]
    invalid = _payload([0], complete=False)
    client = SequenceClient([invalid, invalid, invalid])
    results = review_blocks.run_review(
        client, segments, budget=100, workers=1, max_segment_retries=2
    )
    assert results[0]["complete"] is False
    assert results[0]["error"]
    assert results[0]["attempts"] == 3
    assert len(client.calls) == 3


def test_run_review_runs_blocks_in_parallel_with_a_configured_worker_count() -> None:
    segments = [_segment(0, "One.", "一。"), _segment(1, "Two.", "二。")]
    barrier = threading.Barrier(2)

    class BarrierClient:
        def __init__(self) -> None:
            self.active = 0
            self.max_active = 0
            self._lock = threading.Lock()

        def __call__(self, prompt: str) -> str:
            with self._lock:
                self.active += 1
                self.max_active = max(self.max_active, self.active)
            try:
                barrier.wait(timeout=2)
                return _payload(_prompt_indices(prompt))
            finally:
                with self._lock:
                    self.active -= 1

    client = BarrierClient()
    review_blocks.run_review(client, segments, budget=1, measure=lambda text: 1, workers=2)
    assert client.max_active == 2


def test_run_review_with_one_worker_serializes_blocks() -> None:
    segments = [_segment(i, f"Segment {i}.", f"第{i}段。") for i in range(3)]

    class SlowClient:
        def __init__(self) -> None:
            self.active = 0
            self.max_active = 0
            self._lock = threading.Lock()

        def __call__(self, prompt: str) -> str:
            with self._lock:
                self.active += 1
                self.max_active = max(self.max_active, self.active)
            try:
                time.sleep(0.02)
                return _payload(_prompt_indices(prompt))
            finally:
                with self._lock:
                    self.active -= 1

    client = SlowClient()
    review_blocks.run_review(client, segments, budget=1, measure=lambda text: 1, workers=1)
    assert client.max_active == 1


def test_run_review_rejects_a_non_positive_worker_count() -> None:
    with pytest.raises(ValueError, match="workers"):
        review_blocks.run_review(
            ReviewClient(), [_segment(0, "One.", "一。")], budget=100, workers=0
        )


def test_run_review_reads_segments_from_tmp_path_chapter_state(tmp_path: Path) -> None:
    chapter = {
        "chapter": 1,
        "title": "第一章 开端",
        "heading_level": 1,
        "segments": [
            _segment(0, "Draco waited.", "德拉科等待着。"),
            _segment(1, "Harry looked up.", "哈利抬起头。"),
        ],
    }
    path = segment_state.save_chapter(tmp_path / "source" / "chapters" / "ch-01.json", chapter)
    loaded = segment_state.load_chapter(path)
    client = ReviewClient()
    results = review_blocks.run_review(client, loaded["segments"], budget=100, workers=1)
    assert results[0]["reviewed_segments"] == 2
    assert results[0]["complete"] is True
    assert results[0]["segments"] == [0, 1]
