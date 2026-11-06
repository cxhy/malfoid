# 03: 长段句末切分与 continuation 合并

**What to build:** A translator can keep one logical 段 even when its source text exceeds the per-segment token limit; it is split at sentence boundaries, marked as a continuation, and merged back into a single paragraph on export.

**Blocked by:** 02

**Status:** ready-for-agent

- [ ] A 段 larger than the per-segment token limit splits at sentence boundaries.
- [ ] Continuation parts are marked and keep the parent segment identity.
- [ ] Rendering merges continuation parts back into one paragraph.
- [ ] Splitting and merging do not change the source hash of the logical 段.
- [ ] Offline tests cover an oversized synthetic paragraph, the split boundaries, and the merged output.
