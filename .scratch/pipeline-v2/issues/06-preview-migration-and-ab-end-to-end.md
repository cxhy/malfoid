# 06: 试译 2 块迁移 + A+B 端到端

**What to build:** A translator can move the existing two-block preview into segment state, reuse translations by source hash, retranslate only unmatched segments, and produce a Markdown + HTML preview from the new pipeline.

**Blocked by:** 02, 04, 05

**Status:** ready-for-agent

- [ ] The preview source is re-segmented into chapters and 段.
- [ ] Segments whose source hash matches an existing translation reuse that translation; unmatched segments are retranslated.
- [ ] Migration never reuses text for a segment with a different source hash.
- [ ] The migrated book can be rendered to Markdown and HTML without paragraph loss.
- [ ] A runnable end-to-end command produces the preview from source state.
- [ ] Offline tests cover hash-matched reuse, unmatched retranslation and the rendered output.
