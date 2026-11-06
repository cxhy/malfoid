# 05: 润色批协议

**What to build:** A translator can polish translated 段 under the same equal-length contract, so polishing cannot drop or merge paragraphs; a failed polish leaves the original translation intact.

**Blocked by:** 04

**Status:** resolved

- [x] Polish requests exactly one polished target per input 段.
- [x] Output is validated for array length and completeness.
- [x] A count mismatch is rejected and the original translation is preserved.
- [x] A successful polish writes back by segment identity without changing segment order.
- [x] Polishing never changes paragraph count or drops content.
- [x] Offline tests use a fake model client to cover aligned polish, count mismatch and preservation of the original.

## Comments

- Implemented in `tools/polish_batch.py`, tested in `tests/test_polish_batch.py`.
- Public seam: `plan_polish_batches`, `polish_batch`, `validate_polish_output`, `apply_polished`, `ProtocolError`.
- Tests: 14 ticket tests passed; full suite 106 passed; ruff check/format clean.
- Deferred: glossary and neighbour-context injection into polish prompts belongs to the translation wiring ticket; a separate polished field in segment state is not introduced because polish writes validated results back to `target`.

- Review fix: polish now imports the public `extract_targets` instead of translation's private helper. Successful polish still replaces `target`; pre-polish text remains in the result's `original` field, matching the ticket's accepted comment.
