# 02: 纯文本/Markdown → 章/段状态 + Markdown 重建

**What to build:** A translator can feed a plain-text or Markdown source and get chapters made of 段 with stable identities, persisted as per-chapter state, and rendered back to Markdown without losing structure.

**Blocked by:** 01

**Status:** resolved

- [x] Plain text and Markdown sources split into chapters; blank lines separate 段; headings become chapter titles.
- [x] A chapter title is stored as a heading 段 and is translatable like body text.
- [x] Each 段 records an index, a kind (heading or text), source text, source hash and a nullable target.
- [x] Inline Markdown inside a 段 survives the state round trip.
- [x] Chapter state can be written and read back without changing segment order or content.
- [x] Markdown rendered from segment state reproduces chapter order, heading order and paragraph boundaries.
- [x] Source hashes are stable for identical text and change when the text changes.
- [x] Offline tests cover source reading, state round trip, hash stability and Markdown rendering.

## Comments

- Implemented in commit `49c4315`: `tools/segment_state.py`, `tests/test_source_state.py`.
- Tests: 13 ticket tests passed; full suite 68 passed; ruff check/format clean.
- Review fixes: `render_markdown` naming; numeric chapter ordering (fixes ch-99/ch-100); every heading starts a chapter (no lossy demotion); runtime `verify_round_trip`; re-ingest no longer deletes unrelated chapter state; artifact contract preserved by writing `source/work.md` and refreshing meta; removed the translated-rendering mode.
- Deferred: long-segment split/continuation merge belongs to ticket 03; multi-source ingestion with a chapter offset belongs to ticket 12.
