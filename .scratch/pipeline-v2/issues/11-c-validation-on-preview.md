# 11: C 阶段在试译语料上验收

**What to build:** A translator can run the full 审查 architecture on the existing two-block preview and confirm that alignment false positives are gone before committing to Book 1 mass translation.

**Blocked by:** 06, 08, 09, 10

**Status:** resolved

- [x] Review runs end-to-end on the migrated two-block preview.
- [x] Reported issues trace to real source/target positions; no issue is caused by English/Chinese segment misalignment.
- [x] A false-positive rate is recorded from the preview run.
- [x] The preview review result is used to make a go/no-go decision for Book 1.
- [x] An integration test covers the preview review path without a live model endpoint.

## Comments

- Implemented in `tools/review_preview.py`, tested in `tests/test_review_preview.py`.
- Public seam: `check_alignment`, `load_reuse_map`, `compute_metrics`, `decide_go_no_go`,
  `write_acceptance`, `run_preview_review`; CLI `tools/review_preview.py <preview-book>`.
- Blocks are planned per chapter, so chapter-local segment indices stay unambiguous. Issues
  are checked against segment state before evidence verification; issues anchored on a
  missing target, a non-Chinese target, a stale source hash or a reused-hash mismatch are
  dropped and recorded as alignment false positives. Review, evidence verification and
  arbitration all consume the ticket 08/09/10 public interfaces; formal text is untouched.
- Reports are written under the preview book's `translation/review/` area, including
  `acceptance.json` and `acceptance.md` with the alignment report, false-positive metrics
  and the Book 1 GO/NO-GO decision.
- Tests: 13 ticket tests passed; full suite 207 passed; `ruff check` and
  `ruff format --check` clean. The integration test runs the whole preview review path
  with synthetic chapter fixtures and an injected fake client, without a live endpoint.
- Manual acceptance on the migrated two-block preview: 2 review blocks, 75/75 segments
  reviewed, 5 issues reported, all 5 traceable, 0 alignment false positives, 4 confirmed by
  evidence, 0 rejected, 1 unresolved (bounded evidence could not decide a term-drift
  candidate), content false-positive rate 0.0% (rejected / decided), alignment
  false-positive rate 0.0%, decision GO with human review required for the unresolved item.
- Deferred: the unresolved term-drift candidate needs human adjudication; evidence focus
  terms are still derived by the ticket 09 layer, so a candidate that cites two distant
  positions may not pull the second position into its bounded evidence set.
