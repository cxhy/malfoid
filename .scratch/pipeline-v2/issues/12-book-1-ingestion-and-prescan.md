# 12: Book 1 三段接入 + 预扫描

**What to build:** A translator can ingest Book 1's three serialized segments (chapters 5–17) into chapter/segment state and run prescan for the book, without translating it yet.

**Blocked by:** 02, 07

**Status:** resolved

- [x] Book 1's three source segments become chapters and 段 with correct boundaries.
- [x] Chapter titles, paragraph counts and source hashes match the source.
- [x] Prescan produces chapter digests, a style brief, a synopsis and a book story bible for Book 1.
- [x] Prescan artifacts are cached and reusable.
- [x] No translation is performed in this ticket.
- [x] Offline tests cover the ingested book structure and prescan planning.

## Comments

- Implemented in commit `59dceeb`: `tools/segment_state.py`, `tests/test_book1_ingestion.py`.
- Public seam: `ingest_sources(book_dir, source_paths, chapter_offset=0, max_segment_tokens=...)`; serialized chapter markers are authoritative, marker gaps are preserved, and metadata records all source files, chapter records, per-chapter source hashes, paragraph counts, and `source_complete=true`.
- Tests: 6 ticket tests passed; full suite 98 passed; ruff check/format clean.
- Manual acceptance on the three actual Book 1 source files: 11 chapters (5, 6, 7, 8, 9, 10, 12, 13, 14, 15, 17); paragraph counts 130/390/228/130/296/194/287/165/163/264/262; prescan ready, 11 digests, style brief/synopsis/book bible/series bible written, no translation files. Source content was not committed.
- Deferred: chapter records do not retain per-chapter source-file attribution; stale chapter state from a different prior ingestion is not pruned; prescan style/synopsis/bible remain regenerated from cached digests.

- Review fixes: `source_complete` is set only when `ingest_sources(..., complete=True)`; partial ingest stays not-ready. Stale chapter files are archived under `source/chapters/archive/<timestamp>/` instead of lingering in the live set. Prescan now caches style brief, synopsis and book bible by digest-context hash.
- Book 1 gaps at chapters 11 and 16 reflect the source's own omissions; chapter 5 has no explicit marker, so its title is inferred as `Ch 5`.
