# 07: 每本书预扫描：并行 digest、缓存、系列 bible 合并

**What to build:** A translator can run prescan on a complete book, get chapter digests in parallel with hash-based caching, and end up with a style brief, synopsis and book story bible that merges into the series-level bible.

**Blocked by:** 01, 02

**Status:** ready-for-agent

- [ ] Prescan runs only when the whole book source is present.
- [ ] Chapter digests run in parallel with a configurable worker count (default 2).
- [ ] Digest artifacts are cached by source hash; unchanged chapters are reused without a model call.
- [ ] The book produces a style brief, a synopsis and a book story bible.
- [ ] Completing a book merges its story bible into the series-level bible.
- [ ] Preview translation can skip prescan and use glossary plus neighbour context.
- [ ] Offline tests use a fake model client to cover parallel digest planning, cache reuse and series bible merge.
