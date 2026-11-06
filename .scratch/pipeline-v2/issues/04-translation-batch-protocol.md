# 04: 翻译批协议

**What to build:** A translator can translate a batch of 段 and get exactly one target per source 段; malformed output is retried, then recovered per 段, and every segment ends with a recorded result.

**Blocked by:** 02

**Status:** ready-for-agent

- [ ] Batches are packed by token budget and never exceed the batch budget.
- [ ] Translation requests exactly one target per source 段 as a JSON array.
- [ ] Output is validated for array length, completeness and non-empty content.
- [ ] A protocol error retries up to two additional attempts.
- [ ] After retries, the batch falls back to one call per 段.
- [ ] Every 段 ends with a target or a recorded failure; no segment is silently dropped.
- [ ] Results are written back by segment identity and do not shift other segments.
- [ ] Offline tests use a fake model client to cover success, count mismatch, incomplete output and fallback.
