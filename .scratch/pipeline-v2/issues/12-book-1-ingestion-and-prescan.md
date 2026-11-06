# 12: Book 1 三段接入 + 预扫描

**What to build:** A translator can ingest Book 1's three serialized segments (chapters 5–17) into chapter/segment state and run prescan for the book, without translating it yet.

**Blocked by:** 02, 07

**Status:** ready-for-agent

- [ ] Book 1's three source segments become chapters and 段 with correct boundaries.
- [ ] Chapter titles, paragraph counts and source hashes match the source.
- [ ] Prescan produces chapter digests, a style brief, a synopsis and a book story bible for Book 1.
- [ ] Prescan artifacts are cached and reusable.
- [ ] No translation is performed in this ticket.
- [ ] Offline tests cover the ingested book structure and prescan planning.
