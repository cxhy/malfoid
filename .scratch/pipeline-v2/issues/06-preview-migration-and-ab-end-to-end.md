# 06: 试译 2 块迁移 + A+B 端到端

**What to build:** A translator can move the existing two-block preview into segment state, reuse translations by source hash, retranslate only unmatched segments, and produce a Markdown + HTML preview from the new pipeline.

**Blocked by:** 02, 04, 05

**Status:** resolved

- [x] The preview source is re-segmented into chapters and 段.
- [x] Segments whose source hash matches an existing translation reuse that translation; unmatched segments are retranslated.
- [x] Migration never reuses text for a segment with a different source hash.
- [x] The migrated book can be rendered to Markdown and HTML without paragraph loss.
- [x] A runnable end-to-end command produces the preview from source state.
- [x] Offline tests cover hash-matched reuse, unmatched retranslation and the rendered output.

## Comments

- Implemented `tools/preview_migration.py`, `tools/preview_pipeline.py` and `tests/test_preview_migration.py`.
- Public seam: `build_reuse_map`, `migrate_preview`, `translate_missing`, `polish_pending`, `render_book_markdown`, `render_book_html`, `write_preview`, `run_preview`; CLI `tools/preview_pipeline.py <work> [--chunks ...] [--source ...] [--migrate-only]`.
- Reuse is keyed by source hash, polished before draft. A legacy chunk whose source/translation paragraph counts differ is skipped instead of aligned by position; unmatched segments stay pending and are retranslated through `batch_translate.translate_batch`, then polished through `polish_batch.polish_batch`.
- Rendering merges continuation parts, so Markdown and HTML keep one logical block per segment. Preview HTML is a standalone renderer, independent of Pandoc and the legacy template.
- Tests: 9 ticket tests passed; full suite 177 passed; `ruff check` and `ruff format --check` clean.
- Manual acceptance on the real two-block preview: 39 segments reused from polished chunk0001; chunk0002 was skipped (36 source paragraphs vs 35 translated) and its 36 segments were retranslated and polished live; 75 logical blocks rendered (1 heading + 74 paragraphs), HTML had 74 text paragraphs, zero untranslated targets and no English residue.
- Deferred/risks: equal-count legacy chunks with an internal merge/split can still be positionally misaligned because legacy chunks have no per-segment hash metadata; long segments split into new part hashes are retranslated rather than reusing the legacy full-paragraph target; glossary/neighbour context injection is not wired into the preview batch prompt; preview HTML does not use the legacy Pandoc web template.
