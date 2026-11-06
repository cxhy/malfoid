"""Polish batch protocol tests (ticket 05)."""

from __future__ import annotations

import json

import polish_batch
import pytest


def _segment(index: int, source: str, target: str | None) -> dict:
    return {
        "index": index,
        "kind": "text",
        "source": source,
        "source_hash": f"hash-{index}",
        "target": target,
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


def _payload(*targets: str, complete: bool = True) -> str:
    return json.dumps({"complete": complete, "targets": list(targets)}, ensure_ascii=False)


def test_plan_polish_batches_packs_by_target_budget() -> None:
    segments = [_segment(i, "source", "译" * 4) for i in range(3)]
    batches = polish_batch.plan_polish_batches(segments, 8, measure=len)
    assert [[s["index"] for s in batch] for batch in batches] == [[0, 1], [2]]
    assert all(sum(len(s["target"]) for s in batch) <= 8 for batch in batches)


def test_polish_batch_aligned_output_returns_one_target_per_segment() -> None:
    segments = [
        _segment(0, "One.", "第一段"),
        _segment(1, "Two.", "第二段"),
        _segment(2, "Three.", "第三段"),
    ]
    client = SequenceClient([_payload("润色一", "润色二", "润色三")])
    results = polish_batch.polish_batch(client, segments, budget=2000)
    assert [r["target"] for r in results] == ["润色一", "润色二", "润色三"]
    assert [r["original"] for r in results] == ["第一段", "第二段", "第三段"]
    assert [r["error"] for r in results] == [None, None, None]
    assert len(client.prompts) == 1
    assert "第一段" in client.prompts[0]


def test_polish_batch_accepts_an_object_client() -> None:
    segments = [_segment(0, "One.", "第一段")]
    client = ObjectClient(_payload("润色一"))
    results = polish_batch.polish_batch(client, segments, budget=2000)
    assert results[0]["target"] == "润色一"
    assert len(client.prompts) == 1


def test_polish_batch_retries_a_wrong_count_then_falls_back() -> None:
    segments = [
        _segment(0, "One.", "第一段"),
        _segment(1, "Two.", "第二段"),
        _segment(2, "Three.", "第三段"),
    ]
    wrong = _payload("润色一", "润色二")
    client = SequenceClient(
        [wrong, wrong, wrong, _payload("润色一"), _payload("润色二"), _payload("润色三")]
    )
    results = polish_batch.polish_batch(client, segments, budget=2000)
    assert [r["target"] for r in results] == ["润色一", "润色二", "润色三"]
    assert [r["stage"] for r in results] == ["fallback", "fallback", "fallback"]
    assert len(client.prompts) == 6


def test_polish_batch_rejects_incomplete_output_and_falls_back() -> None:
    segments = [
        _segment(0, "One.", "第一段"),
        _segment(1, "Two.", "第二段"),
    ]
    incomplete = json.dumps({"complete": False, "targets": ["润色一", "润色二"]})
    client = SequenceClient(
        [incomplete, incomplete, incomplete, _payload("润色一"), _payload("润色二")]
    )
    results = polish_batch.polish_batch(client, segments, budget=2000)
    assert [r["target"] for r in results] == ["润色一", "润色二"]
    assert len(client.prompts) == 5


def test_polish_batch_rejects_empty_targets_and_falls_back() -> None:
    segments = [
        _segment(0, "One.", "第一段"),
        _segment(1, "Two.", "第二段"),
        _segment(2, "Three.", "第三段"),
    ]
    empty = _payload("润色一", "", "润色三")
    client = SequenceClient(
        [
            empty,
            empty,
            empty,
            _payload("润色一"),
            _payload("润色二"),
            _payload("润色三"),
        ]
    )
    results = polish_batch.polish_batch(client, segments, budget=2000)
    assert [r["target"] for r in results] == ["润色一", "润色二", "润色三"]
    assert len(client.prompts) == 6


def test_polish_batch_count_mismatch_preserves_the_original_translation() -> None:
    segments = [
        _segment(0, "One.", "第一段"),
        _segment(1, "Two.", "第二段"),
        _segment(2, "Three.", "第三段"),
    ]
    wrong = _payload("润色一", "润色二")
    client = SequenceClient([wrong, wrong, wrong, wrong, wrong, wrong])
    results = polish_batch.polish_batch(client, segments, budget=2000)
    assert [r["target"] for r in results] == [None, None, None]
    assert [r["original"] for r in results] == ["第一段", "第二段", "第三段"]

    chapter = {"chapter": 1, "segments": segments}
    stats = polish_batch.apply_polished(chapter, results)
    assert stats == {"applied": 0, "failed": 3, "skipped": 0}
    assert [s["index"] for s in chapter["segments"]] == [0, 1, 2]
    assert len(chapter["segments"]) == 3
    assert [s["target"] for s in chapter["segments"]] == ["第一段", "第二段", "第三段"]


def test_polish_batch_rejects_malformed_json_and_falls_back() -> None:
    segments = [_segment(0, "One.", "第一段"), _segment(1, "Two.", "第二段")]
    malformed = "not json at all"
    client = SequenceClient(
        [malformed, malformed, malformed, _payload("润色一"), _payload("润色二")]
    )
    results = polish_batch.polish_batch(client, segments, budget=2000)
    assert [r["target"] for r in results] == ["润色一", "润色二"]
    assert [r["stage"] for r in results] == ["fallback", "fallback"]
    assert len(client.prompts) == 5


def test_apply_polished_writes_by_identity_without_reordering() -> None:
    segments = [
        _segment(0, "One.", "第一段"),
        _segment(1, "Two.", "第二段"),
        _segment(2, "Three.", "第三段"),
    ]
    chapter = {"chapter": 1, "segments": segments}
    results = [
        {"index": 2, "source_hash": "hash-2", "target": "润色三"},
        {"index": 1, "source_hash": "hash-1", "target": None, "error": "empty"},
        {"index": 0, "source_hash": "hash-0", "target": "润色一"},
    ]
    stats = polish_batch.apply_polished(chapter, results)
    assert [s["index"] for s in chapter["segments"]] == [0, 1, 2]
    assert [s["target"] for s in chapter["segments"]] == ["润色一", "第二段", "润色三"]
    assert len(chapter["segments"]) == 3
    assert stats == {"applied": 2, "failed": 1, "skipped": 0}


def test_apply_polished_skips_stale_source_hashes() -> None:
    chapter = {"chapter": 1, "segments": [_segment(0, "One.", "第一段")]}
    results = [{"index": 0, "source_hash": "stale-hash", "target": "润色一"}]
    stats = polish_batch.apply_polished(chapter, results)
    assert chapter["segments"][0]["target"] == "第一段"
    assert stats == {"applied": 0, "failed": 0, "skipped": 1}


def test_validate_polish_output_rejects_a_wrong_count() -> None:
    with pytest.raises(polish_batch.ProtocolError, match="expected 3 targets"):
        polish_batch.validate_polish_output(_payload("润色一", "润色二"), 3)


def test_validate_polish_output_accepts_an_aligned_payload() -> None:
    targets = polish_batch.validate_polish_output(_payload("润色一", "润色二"), 2)
    assert targets == ["润色一", "润色二"]


def test_polish_batch_requires_an_existing_translation() -> None:
    segments = [_segment(0, "One.", None)]
    client = SequenceClient([])
    with pytest.raises(ValueError, match="translation"):
        polish_batch.polish_batch(client, segments, budget=2000)
    assert client.prompts == []


def test_polish_batch_propagates_client_errors() -> None:
    segments = [_segment(0, "One.", "第一段")]
    client = SequenceClient([RuntimeError("boom")])
    with pytest.raises(RuntimeError, match="boom"):
        polish_batch.polish_batch(client, segments, budget=2000)
