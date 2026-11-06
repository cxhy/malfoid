# 05: 润色批协议

**What to build:** A translator can polish translated 段 under the same equal-length contract, so polishing cannot drop or merge paragraphs; a failed polish leaves the original translation intact.

**Blocked by:** 04

**Status:** ready-for-agent

- [ ] Polish requests exactly one polished target per input 段.
- [ ] Output is validated for array length and completeness.
- [ ] A count mismatch is rejected and the original translation is preserved.
- [ ] A successful polish writes back by segment identity without changing segment order.
- [ ] Polishing never changes paragraph count or drops content.
- [ ] Offline tests use a fake model client to cover aligned polish, count mismatch and preservation of the original.
