# 09: 审查证据核对 + 冲突仲裁

**What to build:** A translator gets candidate 审查 issues verified against bounded cross-chapter evidence, and contradictory proposals across blocks are grouped and arbitrated instead of being reported as mutually exclusive fixes.

**Blocked by:** 08

**Status:** resolved

- [x] Candidate issues are verified against bounded cross-chapter evidence, up to two rounds.
- [x] Evidence references point to concrete chapter and segment positions.
- [x] Contradictory proposals across blocks are grouped into conflicts.
- [x] Conflicts are arbitrated; unresolved conflicts remain flagged for human decision.
- [x] Offline tests cover evidence selection, conflict grouping and arbitration outcomes.

## Comments

- Implemented in `tools/review_evidence.py`, tested in `tests/test_review_evidence.py`.
- Public seam: `select_evidence`, `verify_candidate`, `verify_candidates`, `collect_candidates`,
  `group_conflicts`, `arbitrate_conflict`, `arbitrate`, `review_with_arbitration`,
  `EvidenceProtocolError`.
- Evidence is bounded by reference count and character budget; each reference carries
  `chapter`, `segment_index` and a `ch-NN:S` position string. Two verification rounds are
  capped by `MAX_EVIDENCE_ROUNDS`.
- Conflicts require distinct blocks and distinct proposals. Candidates may carry `subject`
  or `conflict_key` for cross-position conflicts; otherwise grouping falls back to the
  concrete chapter/segment position. Unresolved arbitration is marked `needs_human`.
- Tests: 30 ticket tests passed; full suite 168 passed; ruff check/format clean.
- Deferred: report artifacts and PASS/FAIL summaries belong to ticket 10; preview
  end-to-end validation belongs to ticket 11.
