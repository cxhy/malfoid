"""Translation batch protocol tests (ticket 04)."""

from __future__ import annotations

import json

import batch_translate
import pytest


def _segment(index: int, source: str, kind: str = "text") -> dict:
    return {
        "index": index,
        "kind": kind,
        "source": source,
        "source_hash": f"hash-{index}",
        "target": None,
    }


class SequenceClient:
    """Offline fake model client returning canned responses in call order."""

    def __init__(self, responses: list) -> None:
        self.responses = list(responses)
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if not self.responses:
            raise AssertionError("unexpected model call")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class ObjectClient:
    """Offline fake model client exposing the object-style boundary."""

    def __init__(self, response: str) -> None:
        self.response = response
        self.prompts: list[str] = []

    def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.response


def _array(*targets: str) -> str:
    return json.dumps(list(targets), ensure_ascii=False)


def _single(target: str) -> str:
    return _array(target)


def test_plan_batch_packs_within_the_budget() -> None:
    segments = [_segment(i, "x" * 4) for i in range(3)]
    batches = batch_translate.plan_batch(segments, 8, measure=len)
    assert [[s["index"] for s in batch] for batch in batches] == [[0, 1], [2]]
    assert all(sum(len(s["source"]) for s in batch) <= 8 for batch in batches)


def test_plan_batch_keeps_every_segment_in_order() -> None:
    segments = [_segment(i, "word") for i in range(5)]
    batches = batch_translate.plan_batch(segments, 10, measure=len)
    assert [s["index"] for batch in batches for s in batch] == [0, 1, 2, 3, 4]


def test_plan_batch_rejects_an_oversized_segment() -> None:
    segments = [_segment(0, "x" * 9)]
    with pytest.raises(ValueError, match="exceeds"):
        batch_translate.plan_batch(segments, 8, measure=len)


def test_translate_batch_success_returns_one_target_per_segment() -> None:
    segments = [_segment(0, "One."), _segment(1, "Two."), _segment(2, "Three.")]
    client = SequenceClient([_array("一", "二", "三")])
    results = batch_translate.translate_batch(client, segments, budget=2000)
    assert [r["target"] for r in results] == ["一", "二", "三"]
    assert [r["error"] for r in results] == [None, None, None]
    assert len(client.prompts) == 1


def test_translate_batch_accepts_an_object_client() -> None:
    segments = [_segment(0, "One.")]
    client = ObjectClient(_array("一"))
    results = batch_translate.translate_batch(client, segments, budget=2000)
    assert results[0]["target"] == "一"
    assert len(client.prompts) == 1


def test_translate_batch_retries_a_wrong_count_then_succeeds() -> None:
    segments = [_segment(0, "One."), _segment(1, "Two."), _segment(2, "Three.")]
    client = SequenceClient([_array("一", "二"), _array("一", "二", "三")])
    results = batch_translate.translate_batch(client, segments, budget=2000)
    assert [r["target"] for r in results] == ["一", "二", "三"]
    assert len(client.prompts) == 2


def test_translate_batch_retries_incomplete_output_then_falls_back() -> None:
    segments = [_segment(0, "One."), _segment(1, "Two.")]
    incomplete = json.dumps({"complete": False, "targets": ["一", "二"]})
    client = SequenceClient([incomplete, incomplete, incomplete, _single("一"), _single("二")])
    results = batch_translate.translate_batch(client, segments, budget=2000)
    assert [r["target"] for r in results] == ["一", "二"]
    assert len(client.prompts) == 5
    assert [r["stage"] for r in results] == ["fallback", "fallback"]


def test_translate_batch_rejects_empty_targets_and_falls_back() -> None:
    segments = [_segment(0, "One."), _segment(1, "Two."), _segment(2, "Three.")]
    empty = _array("一", "", "三")
    client = SequenceClient([empty, empty, empty, _single("一"), _single("二"), _single("三")])
    results = batch_translate.translate_batch(client, segments, budget=2000)
    assert [r["target"] for r in results] == ["一", "二", "三"]
    assert len(client.prompts) == 6


def test_translate_batch_falls_back_to_one_call_per_segment() -> None:
    segments = [_segment(0, "One."), _segment(1, "Two."), _segment(2, "Three.")]
    wrong = _array("一", "二")
    client = SequenceClient([wrong, wrong, wrong, _single("一"), _single("二"), _single("三")])
    results = batch_translate.translate_batch(client, segments, budget=2000)
    assert [r["target"] for r in results] == ["一", "二", "三"]
    assert len(client.prompts) == 6


def test_translate_batch_records_failures_without_dropping_segments() -> None:
    segments = [_segment(0, "One."), _segment(1, "Two."), _segment(2, "Three.")]
    wrong = _array("一", "二")
    client = SequenceClient([wrong, wrong, wrong, _single("一"), _array(""), _single("三")])
    results = batch_translate.translate_batch(client, segments, budget=2000)
    assert [r["index"] for r in results] == [0, 1, 2]
    assert [r["target"] for r in results] == ["一", None, "三"]
    assert results[1]["error"]
    assert results[0]["error"] is None
    assert results[2]["error"] is None


def test_translate_batch_retries_client_errors() -> None:
    segments = [_segment(0, "One.")]
    client = SequenceClient([RuntimeError("boom"), _array("一")])
    results = batch_translate.translate_batch(client, segments, budget=2000)
    assert results[0]["target"] == "一"
    assert len(client.prompts) == 2


def test_translate_batch_packs_multiple_batches_by_budget() -> None:
    segments = [_segment(0, "x" * 4), _segment(1, "y" * 4), _segment(2, "z" * 4)]
    client = SequenceClient([_array("一", "二"), _array("三")])
    results = batch_translate.translate_batch(client, segments, budget=8, measure=len)
    assert [r["target"] for r in results] == ["一", "二", "三"]
    assert len(client.prompts) == 2


def test_apply_targets_writes_by_identity_without_shifting() -> None:
    chapter = {
        "chapter": 1,
        "segments": [
            _segment(0, "One."),
            _segment(1, "Two."),
            _segment(2, "Three."),
        ],
    }
    results = [
        {"index": 2, "source_hash": "hash-2", "target": "三"},
        {"index": 0, "source_hash": "hash-0", "target": "一"},
    ]
    stats = batch_translate.apply_targets(chapter, results)
    assert [s["index"] for s in chapter["segments"]] == [0, 1, 2]
    assert [s["target"] for s in chapter["segments"]] == ["一", None, "三"]
    assert stats == {"applied": 2, "failed": 0, "skipped": 0}


def test_apply_targets_skips_stale_hashes_and_records_failures() -> None:
    chapter = {
        "chapter": 1,
        "segments": [
            _segment(0, "One."),
            _segment(1, "Two."),
        ],
    }
    chapter["segments"][1]["target"] = "旧译"
    results = [
        {"index": 0, "source_hash": "stale-hash", "target": "一"},
        {"index": 1, "source_hash": "hash-1", "target": None, "error": "empty"},
    ]
    stats = batch_translate.apply_targets(chapter, results)
    assert chapter["segments"][0]["target"] is None
    assert chapter["segments"][1]["target"] == "旧译"
    assert stats == {"applied": 0, "failed": 1, "skipped": 1}
