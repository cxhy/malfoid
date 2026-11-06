# 10: 审查报告 + PASS/FAIL

**What to build:** A translator can read 审查 results as structured JSON plus a human-readable summary per block, with a PASS/FAIL summary, and the review never changes formal text.

**Blocked by:** 08, 09

**Status:** ready-for-agent

- [ ] Each review block writes structured JSON and a human-readable Markdown summary.
- [ ] A block-level PASS/FAIL summary is produced deterministically.
- [ ] Review stays advisory: it writes reports and recommendations, never formal text changes.
- [ ] Legacy proofread reports remain readable for completed works.
- [ ] Offline tests cover report writing, summary generation and verdict aggregation.
