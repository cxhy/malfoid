# 04: 翻译批协议

**What to build:** A translator can translate a batch of 段 and get exactly one target per source 段; malformed output is retried, then recovered per 段, and every segment ends with a recorded result.

**Blocked by:** 02

**Status:** resolved

- [x] Batches are packed by token budget and never exceed the batch budget.
- [x] Translation requests exactly one target per source 段 as a JSON array.
- [x] Output is validated for array length, completeness and non-empty content.
- [x] A protocol error retries up to two additional attempts.
- [x] After retries, the batch falls back to one call per 段.
- [x] Every 段 ends with a target or a recorded failure; no segment is silently dropped.
- [x] Results are written back by segment identity and do not shift other segments.
- [x] Offline tests use a fake model client to cover success, count mismatch, incomplete output and fallback.

## Comments

- Implemented in `tools/batch_translate.py`, tested in `tests/test_batch_translate.py`.
- Public seam: `plan_batch`, `translate_batch`, `apply_targets`, `estimate_tokens`, `ProtocolError`.
- Tests: 14 ticket tests passed; full suite 63 passed; ruff check/format clean.
- Deferred: per-segment fallback makes one attempt per segment; bounded per-segment retries and polish reuse belong to ticket 05.

## Comments

- Implemented in commit `d1b1068`.
- Tests: 14 ticket tests passed; full suite 111 passed; ruff check/format clean.
- Review fixes: completeness now requires a JSON object with complete=true and targets; bare arrays are rejected; only protocol errors retry (client errors propagate); an unplannable oversized batch falls back per segment; apply_targets requires index+source_hash; shared token estimator and client adapter.
- Deferred: lower per-segment ceiling is owned by ticket 03's splitter; glossary/context injection belongs to the translation wiring ticket.
