# Tool reference

Every tool is a standalone Python script under `tools/`. Run them with the project
virtualenv; most accept `--help`.

## Source conversion

| Tool | Purpose |
|---|---|
| `ao3_convert.py` | AO3 "Download → HTML" export → `source/work.md` + `meta.json` |
| `fic_convert.py` | FicHub HTML export (`div#chap_N`) → same layout |
| `pdf_convert.py` | chapter-numbered PDF → Markdown, paragraphs rebuilt from indentation |

## Glossary and context

| Tool | Purpose |
|---|---|
| `make_prompt_ctx.py` | per-chunk prompt context: glossary table + neighbour excerpts |
| `export_glossary.py` | human-readable `glossary.md` from `glossary.json` |
| `qa_translation.py` | residue and structure checks over translated chunks |

## Translation

| Tool | Purpose |
|---|---|
| `translate_chunk.py` | translate one chunk; writes `output_chunkXXXX.md` |
| `translate_all.py` | translate pending chunks, repair residue, retry structure failures |
| `fix_residue.py` | rewrite only lines that still contain untranslated Latin words |

## Polish

| Tool | Purpose |
|---|---|
| `polish_chunk.py` | light-touch polish of one chunk, with structural checks |
| `polish_all.py` | polish pending chunks, with a retry ladder and splitting |
| `check_polished.py` | re-run the structural/term/residue checks after manual edits |

## Proofreading

| Tool | Purpose |
|---|---|
| `proofread_chunk.py` | independent fidelity report for one chunk |
| `proofread_all.py` | batch proofreading, resumable, writes `summary.md` |
| `fix_proofread.py` | minimal fixes guided by a proofread report |

## Delivery

| Tool | Purpose |
|---|---|
| `merge_polished.py` | merge polished chunks into `book.md` |
| `build_html.py` | render `book.html` through the bundled template |
| `finalize.py` | copy final HTML/Markdown, move superseded files to `draft/` |
| `split_volumes.py` | split multi-year works into one volume per school year |
| `build_synopsis.py` | generate per-volume synopsis for cover/illustration work |

## Maintenance

| Tool | Purpose |
|---|---|
| `incremental_update.py` | content-addressed rechunking after a source revision |

## Pipeline v2

| Tool | Purpose |
|---|---|
| `preview_migration.py` | migrate a legacy chunk preview into chapter/segment state |
| `preview_pipeline.py` | run preview migration, retranslation and polish end-to-end |
| `review_preview.py` | validate preview review, record false positives and Book 1 GO/NO-GO |
| `review_book_v2.py` | generic whole-book review runner (blocks, evidence, reports) |
| `book_delivery.py` | render translated segment state to `translation/<title>.md/.html` |
| `review_blocks.py` | plan review blocks, validate structured output and bisect |
| `review_evidence.py` | verify review candidates and arbitrate conflicts |
| `review_reports.py` | write review JSON/Markdown reports and PASS/FAIL summaries |

Migration path: run `preview_pipeline.py` on the legacy work with the chunk ids that
already have translations, then validate review with `review_preview.py` before
committing to Book 1 mass translation. Legacy `proofread/` artifacts remain for old
works; v2 review artifacts live under `translation/review/`.

## Vendored skills

The pipeline uses three MIT-licensed agent skills under `.pi/skills/`:

- `translate-book` — chunking, glossary scripts and merge/build helpers;
- `fiction-humanizer-zh` — Chinese fiction polish rules;
- `humanizer-zh` — general Chinese prose polish rules.

See [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md).
