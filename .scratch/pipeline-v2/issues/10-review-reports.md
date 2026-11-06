# 10: 审查报告 + PASS/FAIL

**What to build:** A translator can read 审查 results as structured JSON plus a human-readable summary per block, with a PASS/FAIL summary, and the review never changes formal text.

**Blocked by:** 08, 09

**Status:** resolved

- [x] Each review block writes structured JSON and a human-readable Markdown summary.
- [x] A block-level PASS/FAIL summary is produced deterministically.
- [x] Review stays advisory: it writes reports and recommendations, never formal text changes.
- [x] Legacy proofread reports remain readable for completed works.
- [x] Offline tests cover report writing, summary generation and verdict aggregation.

## Comments

- Implemented in `tools/review_reports.py`, tested in `tests/test_review_reports.py`.
- Public seam: `review_dir`, `block_json_path`, `block_markdown_path`, `summary_json_path`,
  `summary_markdown_path`, `block_verdict`, `build_block_report`, `render_block_markdown`,
  `write_block_report`, `build_summary`, `render_summary_markdown`, `write_review_reports`,
  `read_report`, `read_legacy_proofread`, `read_legacy_summary`.
- Each block writes `translation/review/block-NNN.json` plus `block-NNN.md`; the run also
  writes `summary.json` and `summary.md`. Reports include review issues, evidence
  verification records, arbitration results, recommendations and counts.
- Verdicts are deterministic: incomplete blocks fail; without evidence any issue fails;
  with evidence only confirmed, unresolved or unverified issues fail; unresolved conflicts
  fail. Rejected candidates do not fail a block.
- Reports are advisory and only written under `translation/review`; formal text and legacy
  `translation/proofread` artifacts are left untouched. Legacy proofread reports and
  summaries stay readable through `read_report`, `read_legacy_proofread` and
  `read_legacy_summary`.
- Tests: 17 ticket tests passed; full suite 194 passed; ruff check/format clean.
- Deferred: end-to-end preview review validation belongs to ticket 11.

- Final review fix (`a464cbf`): `block_verdict` matches each issue to its evidence record by segment/message/chapter identity, so a rejected record for another segment cannot pass an issue.
