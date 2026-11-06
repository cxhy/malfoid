# 03: 长段句末切分与 continuation 合并

**What to build:** A translator can keep one logical 段 even when its source text exceeds the per-segment token limit; it is split at sentence boundaries, marked as a continuation, and merged back into a single paragraph on export.

**Blocked by:** 02

**Status:** resolved

- [x] A 段 larger than the per-segment token limit splits at sentence boundaries.
- [x] Continuation parts are marked and keep the parent segment identity.
- [x] Rendering merges continuation parts back into one paragraph.
- [x] Splitting and merging do not change the source hash of the logical 段.
- [x] Offline tests cover an oversized synthetic paragraph, the split boundaries, and the merged output.

## Comments

- Implemented in commit `7be2db1`.
- Tests: 14 ticket tests passed; full suite 111 passed; ruff check/format clean.
- Review fixes: per-part source hash plus logical_source_hash; renamed part_number/logical_index; continuation merging now goes through one logical-block function used by render, chapter text and round-trip; shared token estimator.
- Deferred: headings are exempt from splitting; a single oversized sentence stays whole; CJK sentence punctuation not handled.
