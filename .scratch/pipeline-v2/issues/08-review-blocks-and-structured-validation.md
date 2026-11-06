# 08: 审查块规划 + 结构化输出校验

**What to build:** A translator can run 审查 over contiguous segments packed into fixed token-budget blocks in parallel, with structured output that rejects wrong counts and incomplete payloads, and bisects only when output is invalid.

**Blocked by:** 02, 04

**Status:** ready-for-agent

- [ ] Blocks pack contiguous 段 to roughly three translation batches of tokens.
- [ ] Blocks run in parallel with a configurable worker count (default 2).
- [ ] Review output is structured JSON with issues, a reviewed segment count and a completeness flag.
- [ ] Output with a wrong reviewed count, missing completeness or malformed shape is rejected.
- [ ] Invalid output triggers bisection; bisection is bounded and does not become an exponential ladder.
- [ ] Per-segment retries inside a block are bounded.
- [ ] Offline tests use a fake model client to cover block planning, valid output, wrong count and bisection.
