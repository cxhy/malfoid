"""Review evidence verification and conflict arbitration tests (ticket 09)."""

from __future__ import annotations

import copy
import json
import re

import pytest
import review_blocks
import review_evidence

_INDEX_RE = re.compile(r"^\[(\d+)\]$", re.MULTILINE)
_CANDIDATE_RE = re.compile(r"^\[(cand-[A-Za-z0-9-]+)\]", re.MULTILINE)


def _segment(
    index: int,
    source: str,
    target: str | None = None,
    *,
    chapter: int | None = None,
    kind: str = "text",
) -> dict:
    segment = {
        "index": index,
        "kind": kind,
        "source": source,
        "source_hash": f"hash-{chapter}-{index}",
        "target": target,
    }
    if chapter is not None:
        segment["chapter"] = chapter
    return segment


def _chapter(number: int, segments: list[dict]) -> dict:
    return {
        "chapter": number,
        "title": f"第{number}章",
        "heading_level": 1,
        "segments": segments,
    }


def _candidate(
    *,
    chapter: int = 1,
    segment_index: int = 0,
    message: str = "术语漂移：称呼不一致。",
    proposal: str | None = None,
    block: int = 0,
    candidate_id: str | None = None,
    subject: str | None = None,
    focus_terms: list[str] | None = None,
) -> dict:
    candidate = {
        "chapter": chapter,
        "segment_index": segment_index,
        "message": message,
        "proposal": proposal if proposal is not None else message,
        "block": block,
    }
    if candidate_id is not None:
        candidate["id"] = candidate_id
    if subject is not None:
        candidate["subject"] = subject
    if focus_terms is not None:
        candidate["focus_terms"] = focus_terms
    return candidate


def _review_payload(indices: list[int], issues: list[dict] | None = None) -> str:
    return json.dumps(
        {
            "issues": issues or [],
            "reviewed_segments": len(indices),
            "complete": True,
        },
        ensure_ascii=False,
    )


def _verification_payload(
    verdict: str, reason: str = "核对完成。", requested: list[str] | None = None
):
    return json.dumps(
        {
            "verdict": verdict,
            "reason": reason,
            "requested_terms": requested or [],
        },
        ensure_ascii=False,
    )


def _arbitration_payload(decision: str, reason: str = "证据更支持该提案。"):
    return json.dumps({"decision": decision, "reason": reason}, ensure_ascii=False)


def _prompt_indices(prompt: str) -> list[int]:
    return [int(value) for value in _INDEX_RE.findall(prompt)]


class SequenceClient:
    """Offline fake returning canned responses in call order."""

    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.calls: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.calls.append(prompt)
        if not self.responses:
            raise AssertionError("unexpected model call")
        return self.responses.pop(0)


class TaskClient:
    """Offline fake that answers evidence and arbitration tasks deterministically."""

    def __init__(self, verdict: str = "confirmed", decision: str | None = None) -> None:
        self.verdict = verdict
        self.decision = decision
        self.calls: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.calls.append(prompt)
        if "TASK: VERIFY_REVIEW_ISSUE" in prompt:
            return _verification_payload(self.verdict)
        if "TASK: ARBITRATE_REVIEW_CONFLICT" in prompt:
            ids = _CANDIDATE_RE.findall(prompt)
            if self.decision == "unresolved":
                return _arbitration_payload("unresolved", "证据不足，留人工裁定。")
            if self.decision == "first":
                chosen = ids[0] if ids else "unresolved"
            else:
                chosen = self.decision or (ids[0] if ids else "unresolved")
            return _arbitration_payload(chosen)
        raise AssertionError("unexpected model task")

    def tasks(self) -> list[str]:
        return ["verify" if "VERIFY_REVIEW_ISSUE" in call else "arbitrate" for call in self.calls]


class ReviewClient:
    """Offline review fake returning one subject-tagged issue per block."""

    def __init__(self, issues_by_index: dict[int, dict]) -> None:
        self.issues_by_index = issues_by_index
        self.calls: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.calls.append(prompt)
        indices = _prompt_indices(prompt)
        index = indices[0]
        default = {"message": "审查问题。", "proposal": "修改建议。"}
        issue = {"segment_index": index, **self.issues_by_index.get(index, default)}
        return _review_payload(indices, [issue])


def _chapters_with_draco() -> list[dict]:
    return [
        _chapter(
            1,
            [
                _segment(0, "Draco waited on the platform.", "德拉科在站台上等待。"),
                _segment(1, "Harry looked away.", "哈利移开了视线。"),
            ],
        ),
        _chapter(
            2,
            [
                _segment(0, "Draco watched the train.", "德拉科看着火车。"),
                _segment(1, "The train left.", "火车离开了。"),
            ],
        ),
        _chapter(
            3,
            [
                _segment(0, "Nobody spoke.", "没有人说话。"),
                _segment(1, "Draco turned back.", "德拉科转过身。"),
            ],
        ),
    ]


def test_select_evidence_returns_the_anchor_with_a_concrete_position() -> None:
    chapters = _chapters_with_draco()
    candidate = _candidate(chapter=2, segment_index=0)
    refs = review_evidence.select_evidence(candidate, chapters, focus_terms=["Draco"])
    anchor = refs[0]
    assert anchor["chapter"] == 2
    assert anchor["segment_index"] == 0
    assert anchor["ref"] == "ch-02:0"
    assert anchor["source"] == "Draco watched the train."
    assert anchor["target"] == "德拉科看着火车。"
    assert anchor["reason"] == "candidate anchor"


def test_select_evidence_prefers_bounded_cross_chapter_occurrences() -> None:
    chapters = _chapters_with_draco()
    candidate = _candidate(chapter=2, segment_index=0)
    refs = review_evidence.select_evidence(
        candidate, chapters, focus_terms=["Draco"], max_refs=3, max_chars=1000
    )
    assert [(ref["chapter"], ref["segment_index"]) for ref in refs] == [
        (2, 0),
        (1, 0),
        (3, 1),
    ]
    assert all(ref["ref"] == f"ch-{ref['chapter']:02d}:{ref['segment_index']}" for ref in refs)


def test_select_evidence_bounds_reference_count_and_characters() -> None:
    chapters = _chapters_with_draco()
    candidate = _candidate(chapter=2, segment_index=0)
    refs = review_evidence.select_evidence(
        candidate, chapters, focus_terms=["Draco"], max_refs=2, max_chars=1000
    )
    assert len(refs) == 2
    assert sum(len(ref["source"]) + len(ref["target"] or "") for ref in refs) <= 1000


def test_select_evidence_derives_english_names_when_focus_terms_are_absent() -> None:
    chapters = _chapters_with_draco()
    candidate = _candidate(chapter=2, segment_index=0)
    refs = review_evidence.select_evidence(candidate, chapters, max_refs=4, max_chars=1000)
    positions = [(ref["chapter"], ref["segment_index"]) for ref in refs]
    assert (1, 0) in positions
    assert (3, 1) in positions
    assert all(ref["chapter"] == 2 and ref["segment_index"] == 0 for ref in refs[:1])


def test_select_evidence_excludes_already_selected_references() -> None:
    chapters = _chapters_with_draco()
    candidate = _candidate(chapter=2, segment_index=0)
    first = review_evidence.select_evidence(candidate, chapters, focus_terms=["Draco"])
    extra = review_evidence.select_evidence(
        candidate,
        chapters,
        focus_terms=["Draco"],
        exclude=first,
        include_neighbours=True,
    )
    first_keys = {(ref["chapter"], ref["segment_index"]) for ref in first}
    assert all((ref["chapter"], ref["segment_index"]) not in first_keys for ref in extra)


def test_select_evidence_rejects_an_unknown_candidate_position() -> None:
    with pytest.raises(ValueError, match="chapter 9"):
        review_evidence.select_evidence(
            _candidate(chapter=9, segment_index=0), _chapters_with_draco()
        )


def test_select_evidence_rejects_non_positive_bounds() -> None:
    chapters = _chapters_with_draco()
    candidate = _candidate()
    with pytest.raises(ValueError, match="max_refs"):
        review_evidence.select_evidence(candidate, chapters, max_refs=0)
    with pytest.raises(ValueError, match="max_chars"):
        review_evidence.select_evidence(candidate, chapters, max_chars=0)


def test_verify_candidate_confirms_after_one_round() -> None:
    client = SequenceClient([_verification_payload("confirmed", "跨章称呼一致。")])
    result = review_evidence.verify_candidate(
        client, _candidate(chapter=2, segment_index=0), _chapters_with_draco()
    )
    assert result["status"] == "confirmed"
    assert result["verified"] is True
    assert result["needs_human"] is False
    assert result["rounds"] == 1
    assert result["reason"] == "跨章称呼一致。"
    assert result["evidence"]
    assert "TASK: VERIFY_REVIEW_ISSUE" in client.calls[0]
    assert "Round: 1 of 2" in client.calls[0]
    assert "ch-02:0" in client.calls[0]


def test_verify_candidate_rejects_after_one_round() -> None:
    client = SequenceClient([_verification_payload("rejected", "证据不支持该问题。")])
    result = review_evidence.verify_candidate(
        client, _candidate(chapter=2, segment_index=0), _chapters_with_draco()
    )
    assert result["status"] == "rejected"
    assert result["verified"] is False
    assert result["needs_human"] is False
    assert result["rounds"] == 1


def test_verify_candidate_requests_more_evidence_then_confirms() -> None:
    client = SequenceClient(
        [
            _verification_payload("need_more", "还需要哈利相关段落。", ["Harry"]),
            _verification_payload("confirmed", "补充证据后确认。"),
        ]
    )
    result = review_evidence.verify_candidate(
        client, _candidate(chapter=2, segment_index=0), _chapters_with_draco()
    )
    assert result["status"] == "confirmed"
    assert result["rounds"] == 2
    assert len(client.calls) == 2
    assert "Round: 2 of 2" in client.calls[1]
    positions = {(ref["chapter"], ref["segment_index"]) for ref in result["evidence"]}
    assert (1, 1) in positions
    assert result["requested_terms"] == ["Harry"]


def test_verify_candidate_marks_unresolved_after_two_rounds() -> None:
    client = SequenceClient(
        [
            _verification_payload("need_more", "还需要更多证据。", ["Harry"]),
            _verification_payload("need_more", "仍然不足。", ["Harry"]),
        ]
    )
    result = review_evidence.verify_candidate(
        client, _candidate(chapter=2, segment_index=0), _chapters_with_draco()
    )
    assert result["status"] == "unresolved"
    assert result["verified"] is False
    assert result["needs_human"] is True
    assert result["rounds"] == 2
    assert len(client.calls) == 2
    assert result["reason"] == "仍然不足。"


def test_verify_candidate_marks_unresolved_on_invalid_model_output() -> None:
    client = SequenceClient(["not json"])
    result = review_evidence.verify_candidate(
        client, _candidate(chapter=2, segment_index=0), _chapters_with_draco()
    )
    assert result["status"] == "unresolved"
    assert result["needs_human"] is True
    assert result["error"]
    assert result["rounds"] == 1


def test_verify_candidate_rejects_more_than_two_rounds() -> None:
    with pytest.raises(ValueError, match="max_rounds"):
        review_evidence.verify_candidate(
            SequenceClient([]), _candidate(), _chapters_with_draco(), max_rounds=3
        )
    with pytest.raises(ValueError, match="max_rounds"):
        review_evidence.verify_candidate(
            SequenceClient([]), _candidate(), _chapters_with_draco(), max_rounds=0
        )


def test_verify_candidates_preserves_candidate_order() -> None:
    client = TaskClient(verdict="confirmed")
    candidates = [
        _candidate(chapter=1, segment_index=0, message="第一个问题。"),
        _candidate(chapter=2, segment_index=0, message="第二个问题。"),
    ]
    results = review_evidence.verify_candidates(client, candidates, _chapters_with_draco())
    assert [result["candidate"]["message"] for result in results] == [
        "第一个问题。",
        "第二个问题。",
    ]
    assert [result["status"] for result in results] == ["confirmed", "confirmed"]
    assert len(client.calls) == 2


def test_collect_candidates_uses_review_blocks_results_and_block_context() -> None:
    segments = [
        _segment(0, "Draco waited.", "德拉科等待着。", chapter=1),
        _segment(1, "Harry looked.", "哈利看着。", chapter=2),
    ]
    blocks = review_blocks.plan_blocks(segments, 100)
    client = ReviewClient(
        {
            0: {
                "message": "称呼漂移：德拉科。",
                "proposal": "统一译为德拉科。",
                "subject": "Draco",
            }
        }
    )
    reviews = review_blocks.run_review(client, segments, budget=100, workers=1)
    candidates = review_evidence.collect_candidates(reviews, blocks)
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate["chapter"] == 1
    assert candidate["segment_index"] == 0
    assert candidate["block"] == 0
    assert candidate["message"] == "称呼漂移：德拉科。"
    assert candidate["proposal"] == "统一译为德拉科。"
    assert candidate["subject"] == "Draco"
    assert candidate["id"].startswith("cand-")


def test_collect_candidates_rejects_mismatched_review_and_block_counts() -> None:
    segments = [_segment(0, "One.", "一。", chapter=1)]
    blocks = review_blocks.plan_blocks(segments, 100)
    reviews = review_blocks.run_review(ReviewClient({}), segments, budget=100, workers=1)
    with pytest.raises(ValueError, match="same length"):
        review_evidence.collect_candidates(reviews, blocks + blocks)


def test_collect_candidates_requires_chapter_context() -> None:
    segments = [_segment(0, "One.", "一。")]
    blocks = review_blocks.plan_blocks(segments, 100)
    reviews = review_blocks.run_review(ReviewClient({}), segments, budget=100, workers=1)
    with pytest.raises(ValueError, match="chapter"):
        review_evidence.collect_candidates(reviews, blocks)


def test_group_conflicts_groups_contradictory_proposals_across_blocks() -> None:
    candidates = [
        _candidate(segment_index=0, proposal="统一译为德拉科。", block=0),
        _candidate(segment_index=0, proposal="统一译为马尔福。", block=1),
    ]
    conflicts = review_evidence.group_conflicts(candidates)
    assert len(conflicts) == 1
    conflict = conflicts[0]
    assert conflict["key"] == "ch01:s0"
    assert conflict["blocks"] == ["0", "1"]
    assert [proposal["proposal"] for proposal in conflict["proposals"]] == [
        "统一译为德拉科。",
        "统一译为马尔福。",
    ]
    assert len(conflict["candidates"]) == 2


def test_group_conflicts_uses_a_subject_key_across_positions() -> None:
    candidates = [
        _candidate(
            chapter=1,
            segment_index=0,
            subject="Draco",
            proposal="统一译为德拉科。",
            block=0,
        ),
        _candidate(
            chapter=2,
            segment_index=1,
            subject="Draco",
            proposal="统一译为马尔福。",
            block=1,
        ),
    ]
    conflicts = review_evidence.group_conflicts(candidates)
    assert len(conflicts) == 1
    assert conflicts[0]["key"] == "subject:Draco"
    assert conflicts[0]["blocks"] == ["0", "1"]


def test_group_conflicts_ignores_agreeing_proposals() -> None:
    candidates = [
        _candidate(segment_index=0, proposal="统一译为德拉科。", block=0),
        _candidate(segment_index=0, proposal="统一译为德拉科。", block=1),
    ]
    assert review_evidence.group_conflicts(candidates) == []


def test_group_conflicts_requires_distinct_blocks() -> None:
    candidates = [
        _candidate(segment_index=0, proposal="统一译为德拉科。", block=0),
        _candidate(segment_index=0, proposal="统一译为马尔福。", block=0),
    ]
    assert review_evidence.group_conflicts(candidates) == []


def test_group_conflicts_includes_unconfirmed_verification_records() -> None:
    records = [
        {
            "candidate": _candidate(segment_index=0, proposal="统一译为德拉科。", block=0),
            "status": "confirmed",
            "evidence": [],
        },
        {
            "candidate": _candidate(segment_index=0, proposal="统一译为马尔福。", block=1),
            "status": "rejected",
            "evidence": [],
        },
    ]
    conflicts = review_evidence.group_conflicts(records)
    assert len(conflicts) == 1
    assert conflicts[0]["key"] == "ch01:s0"
    assert conflicts[0]["blocks"] == ["0", "1"]


def test_group_conflicts_can_still_restrict_to_verified_records() -> None:
    records = [
        {
            "candidate": _candidate(segment_index=0, proposal="统一译为德拉科。", block=0),
            "status": "confirmed",
            "evidence": [],
        },
        {
            "candidate": _candidate(segment_index=0, proposal="统一译为马尔福。", block=1),
            "status": "rejected",
            "evidence": [],
        },
    ]
    assert review_evidence.group_conflicts(records, verified_only=True) == []


def test_group_conflicts_groups_rejected_subjects_across_positions() -> None:
    records = [
        {
            "candidate": _candidate(
                chapter=1,
                segment_index=0,
                subject="Draco",
                proposal="统一译为德拉科。",
                block=0,
            ),
            "status": "rejected",
            "evidence": [],
        },
        {
            "candidate": _candidate(
                chapter=2,
                segment_index=1,
                subject="Draco",
                proposal="统一译为马尔福。",
                block=1,
            ),
            "status": "unresolved",
            "evidence": [],
        },
    ]
    conflicts = review_evidence.group_conflicts(records)
    assert len(conflicts) == 1
    assert conflicts[0]["key"] == "subject:Draco"


def test_group_conflicts_keeps_conflict_ids_deterministic() -> None:
    candidates = [
        _candidate(segment_index=0, proposal="统一译为德拉科。", block=0),
        _candidate(segment_index=0, proposal="统一译为马尔福。", block=1),
    ]
    first = review_evidence.group_conflicts(candidates)
    second = review_evidence.group_conflicts(list(reversed(candidates)))
    assert first == second
    assert first[0]["id"].startswith("conflict-")


def test_arbitrate_resolves_a_conflict_to_one_candidate() -> None:
    candidates = [
        _candidate(candidate_id="cand-a", segment_index=0, proposal="统一译为德拉科。", block=0),
        _candidate(candidate_id="cand-b", segment_index=0, proposal="统一译为马尔福。", block=1),
    ]
    conflict = review_evidence.group_conflicts(candidates)[0]
    client = TaskClient(decision="cand-a")
    results = review_evidence.arbitrate(client, [conflict])
    assert len(results) == 1
    result = results[0]
    assert result["conflict"] == conflict["id"]
    assert result["resolved"] is True
    assert result["winner"] == "cand-a"
    assert result["needs_human"] is False
    assert result["reason"]
    assert "TASK: ARBITRATE_REVIEW_CONFLICT" in client.calls[0]
    assert "[cand-a]" in client.calls[0]
    assert "[cand-b]" in client.calls[0]


def test_arbitrate_keeps_an_unresolved_conflict_for_human_decision() -> None:
    candidates = [
        _candidate(candidate_id="cand-a", segment_index=0, proposal="统一译为德拉科。", block=0),
        _candidate(candidate_id="cand-b", segment_index=0, proposal="统一译为马尔福。", block=1),
    ]
    conflict = review_evidence.group_conflicts(candidates)[0]
    client = TaskClient(decision="unresolved")
    result = review_evidence.arbitrate(client, [conflict])[0]
    assert result["resolved"] is False
    assert result["winner"] is None
    assert result["needs_human"] is True
    assert result["reason"] == "证据不足，留人工裁定。"


def test_arbitrate_flags_an_invalid_decision_as_unresolved() -> None:
    candidates = [
        _candidate(candidate_id="cand-a", segment_index=0, proposal="统一译为德拉科。", block=0),
        _candidate(candidate_id="cand-b", segment_index=0, proposal="统一译为马尔福。", block=1),
    ]
    conflict = review_evidence.group_conflicts(candidates)[0]
    client = TaskClient(decision="cand-missing")
    result = review_evidence.arbitrate(client, [conflict])[0]
    assert result["resolved"] is False
    assert result["winner"] is None
    assert result["needs_human"] is True
    assert result["error"]


def test_arbitrate_flags_malformed_output_as_unresolved() -> None:
    candidates = [
        _candidate(candidate_id="cand-a", segment_index=0, proposal="统一译为德拉科。", block=0),
        _candidate(candidate_id="cand-b", segment_index=0, proposal="统一译为马尔福。", block=1),
    ]
    conflict = review_evidence.group_conflicts(candidates)[0]
    client = SequenceClient(["not json"])
    result = review_evidence.arbitrate(client, [conflict])[0]
    assert result["resolved"] is False
    assert result["needs_human"] is True
    assert result["error"]


def test_arbitrate_preserves_conflict_order() -> None:
    first = [
        _candidate(candidate_id="cand-a1", segment_index=0, proposal="统一译为德拉科。", block=0),
        _candidate(candidate_id="cand-a2", segment_index=0, proposal="统一译为马尔福。", block=1),
    ]
    second = [
        _candidate(candidate_id="cand-b1", segment_index=1, proposal="保留原称呼。", block=0),
        _candidate(candidate_id="cand-b2", segment_index=1, proposal="改为教名。", block=1),
    ]
    conflicts = review_evidence.group_conflicts(first + second)
    client = TaskClient(decision="cand-a1")
    results = review_evidence.arbitrate(client, conflicts)
    assert [result["conflict"] for result in results] == [conflict["id"] for conflict in conflicts]
    assert len(results) == 2


def test_review_with_arbitration_runs_review_evidence_and_arbitration_end_to_end() -> None:
    chapters = _chapters_with_draco()
    segments = [
        _segment(0, "Draco waited on the platform.", "德拉科在站台上等待。", chapter=1),
        _segment(1, "Draco turned back.", "德拉科转过身。", chapter=3),
    ]
    blocks = review_blocks.plan_blocks(segments, 40, measure=len)
    review_client = ReviewClient(
        {
            0: {
                "message": "称呼漂移：德拉科。",
                "proposal": "统一译为德拉科。",
                "subject": "Draco",
            },
            1: {
                "message": "称呼漂移：马尔福。",
                "proposal": "统一译为马尔福。",
                "subject": "Draco",
            },
        }
    )
    reviews = review_blocks.run_review(review_client, segments, budget=40, measure=len, workers=1)
    evidence_client = TaskClient(decision="first")
    before = copy.deepcopy(chapters)
    result = review_evidence.review_with_arbitration(evidence_client, reviews, blocks, chapters)
    assert len(result["confirmed"]) == 2
    assert len(result["conflicts"]) == 1
    assert result["conflicts"][0]["resolved"] is True
    assert result["unresolved_conflicts"] == []
    assert [call["conflict"] for call in result["conflicts"]] == [
        conflict["id"] for conflict in review_evidence.group_conflicts(result["confirmed"])
    ]
    assert chapters == before
    assert evidence_client.tasks() == ["verify", "verify", "arbitrate"]


def test_review_with_arbitration_keeps_unresolved_conflicts_flagged() -> None:
    chapters = _chapters_with_draco()
    segments = [
        _segment(0, "Draco waited on the platform.", "德拉科在站台上等待。", chapter=1),
        _segment(1, "Draco turned back.", "德拉科转过身。", chapter=3),
    ]
    blocks = review_blocks.plan_blocks(segments, 40, measure=len)
    review_client = ReviewClient(
        {
            0: {
                "message": "称呼漂移：德拉科。",
                "proposal": "统一译为德拉科。",
                "subject": "Draco",
            },
            1: {
                "message": "称呼漂移：马尔福。",
                "proposal": "统一译为马尔福。",
                "subject": "Draco",
            },
        }
    )
    reviews = review_blocks.run_review(review_client, segments, budget=40, measure=len, workers=1)
    evidence_client = TaskClient(decision="unresolved")
    result = review_evidence.review_with_arbitration(evidence_client, reviews, blocks, chapters)
    assert len(result["conflicts"]) == 1
    assert result["conflicts"][0]["resolved"] is False
    assert len(result["unresolved_conflicts"]) == 1
    assert result["unresolved_conflicts"][0]["needs_human"] is True


def test_review_with_arbitration_keeps_unverified_contradictions() -> None:
    chapters = _chapters_with_draco()
    segments = [
        _segment(0, "Draco waited on the platform.", "德拉科在站台上等待。", chapter=1),
        _segment(1, "Draco turned back.", "德拉科转过身。", chapter=3),
    ]
    blocks = review_blocks.plan_blocks(segments, 40, measure=len)
    review_client = ReviewClient(
        {
            0: {
                "message": "称呼漂移：德拉科。",
                "proposal": "统一译为德拉科。",
                "subject": "Draco",
            },
            1: {
                "message": "称呼漂移：马尔福。",
                "proposal": "统一译为马尔福。",
                "subject": "Draco",
            },
        }
    )
    reviews = review_blocks.run_review(review_client, segments, budget=40, measure=len, workers=1)
    evidence_client = TaskClient(verdict="rejected", decision="unresolved")
    result = review_evidence.review_with_arbitration(evidence_client, reviews, blocks, chapters)
    assert len(result["rejected"]) == 2
    assert len(result["conflicts"]) == 1
    assert result["conflicts"][0]["needs_human"] is True
    assert len(result["unresolved_conflicts"]) == 1
