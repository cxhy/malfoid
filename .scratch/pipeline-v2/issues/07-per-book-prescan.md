# 07: 每本书预扫描：并行 digest、缓存、系列 bible 合并

**What to build:** A translator can run prescan on a complete book, get chapter digests in parallel with hash-based caching, and end up with a style brief, synopsis and book story bible that merges into the series-level bible.

**Blocked by:** 01, 02

**Status:** resolved

- [x] Prescan runs only when the whole book source is present.
- [x] Chapter digests run in parallel with a configurable worker count (default 2).
- [x] Digest artifacts are cached by source hash; unchanged chapters are reused without a model call.
- [x] The book produces a style brief, a synopsis and a book story bible.
- [x] Completing a book merges its story bible into the series-level bible.
- [x] Preview translation can skip prescan and use glossary plus neighbour context.
- [x] Offline tests use a fake model client to cover parallel digest planning, cache reuse and series bible merge.

## Comments

- Implemented in commit `cc34299`.
- Tests: 11 ticket tests passed; full suite 111 passed; ruff check/format clean.
- Review fixes: readiness now requires an explicit source_complete marker; digest input and hash use merged logical blocks; writes chapter_digests/ch-NN.md and book_synopsis.md for the context builder; auto-infers the series root and merges the bible; shared client adapter.
- Deferred: preview context builder belongs to the preview ticket; style/synopsis/bible are regenerated from cached digests rather than cached themselves.
