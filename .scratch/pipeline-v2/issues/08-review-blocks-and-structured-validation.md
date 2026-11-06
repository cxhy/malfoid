# 08: 审查块规划 + 结构化输出校验

**What to build:** A translator can run 审查 over contiguous segments packed into fixed token-budget blocks in parallel, with structured output that rejects wrong counts and incomplete payloads, and bisects only when output is invalid.

**Blocked by:** 02, 04

**Status:** resolved

- [x] Blocks pack contiguous 段 to roughly three translation batches of tokens.
- [x] Blocks run in parallel with a configurable worker count (default 2).
- [x] Review output is structured JSON with issues, a reviewed segment count and a completeness flag.
- [x] Output with a wrong reviewed count, missing completeness or malformed shape is rejected.
- [x] Invalid output triggers bisection; bisection is bounded and does not become an exponential ladder.
- [x] Per-segment retries inside a block are bounded.
- [x] Offline tests use a fake model client to cover block planning, valid output, wrong count and bisection.

## Comments

- Implemented in `tools/review_blocks.py`, tested in `tests/test_review_blocks.py`.
- Public seam: `plan_blocks`, `validate_review_output`, `build_review_prompt`, `review_block`, `run_review`, `ReviewProtocolError`.
- Tests: 24 ticket tests passed; full suite 116 passed; ruff check/format clean.
- Deferred: evidence verification and conflict arbitration belong to ticket 09; review report artifacts and PASS/FAIL summaries belong to ticket 10.

- Review fixes: block budget now measures source plus target tokens; issue `segment_index` must be inside the reviewed block; `run_review` accepts an optional `cache_dir` and reuses complete block results for resume.
- Reports, evidence verification and conflict arbitration remain tickets 09/10.
