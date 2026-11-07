# Tool reference

Every tool is a standalone Python script under `tools/`. Run them with the project
virtualenv; most accept `--help`.

> **v2 is the active pipeline.** v1 tools marked archived now live under
> `archive/v1/tools/` and are not used. Converters, `build_html.py` and
> `export_glossary.py` remain active.

## Source conversion

| Tool | Purpose |
|---|---|
| `ao3_convert.py` | AO3 "Download → HTML" export → `source/work.md` + `meta.json` |
| `fic_convert.py` | FicHub HTML export (`div#chap_N`) → same layout |
| `pdf_convert.py` | chapter-numbered PDF → Markdown, paragraphs rebuilt from indentation |

## Glossary and context (v1; export_glossary remains active)

| Tool | Purpose |
|---|---|
| `build_prescan.py` | resumable chapter digests, style brief, synopsis and HP story bible |
| `make_prompt_ctx.py` | per-chunk prompt context: style brief, story bible, synopsis, digest, glossary, rolling context, neighbour excerpts |
| `prompt_profile.py` | load the active HP fanfic profile and its per-stage rule sections |
| `export_glossary.py` | human-readable `glossary.md` from `glossary.json` |
| `qa_translation.py` | residue and structure checks over translated chunks |

## Translation (v1, archived)

| Tool | Purpose |
|---|---|
| `translate_chunk.py` | translate one chunk; writes `output_chunkXXXX.md` |
| `translate_all.py` | translate pending chunks, repair residue, retry structure failures |
| `fix_residue.py` | rewrite only lines that still contain untranslated Latin words |

## Polish (v1, archived)

| Tool | Purpose |
|---|---|
| `polish_chunk.py` | light-touch polish of one chunk, with structural checks |
| `polish_all.py` | polish pending chunks with a 1→2→4→8 split ladder; `--recheck` validates existing polished files and retries broken ones |
| `check_polished.py` | re-run the structural/term/residue checks after manual edits |

## Proofreading (v1, archived)

| Tool | Purpose |
|---|---|
| `proofread_chunk.py` | independent fidelity report for one chunk |
| `proofread_all.py` | batch proofreading, resumable; reports without a PASS/FAIL verdict are retried, writes `summary.md` |
| `fix_proofread.py` | minimal fixes guided by a proofread report |
| `review_book.py` | whole-book continuity review: term/pronoun/timeline/status/reveal drift, with cross-chapter evidence and conflict list |

## Delivery (v1; build_html remains active)

| Tool | Purpose |
|---|---|
| `merge_polished.py` | merge polished chunks into `book.md` |
| `build_html.py` | render `book.html` through the bundled template |
| `finalize.py` | copy final HTML/Markdown, move superseded files to `draft/` |
| `split_volumes.py` | split multi-year works into one volume per school year |
| `build_synopsis.py` | generate per-volume synopsis for cover/illustration work |

## Profiles

`profiles/hp-fanfic/` holds the project’s translation profile: one manifest and Markdown
rule files for translation, polish, proofread, chapter digest, story bible and book
review. The profile is resolved from `MALFOID_PROFILE`, `meta.json`’s `profile` field, or
the default `hp-fanfic`.

## Maintenance (v1, archived)

| Tool | Purpose |
|---|---|
| `incremental_update.py` | content-addressed rechunking after a source revision |

## Pipeline v2

| Tool | Purpose |
|---|---|
| `ingest_book_v2.py` | create a series/book and ingest source files into chapter state |
| `txt_convert.py` | convert chapter-numbered TXT into Markdown + `meta.json` |
| `translate_book_v2.py` | translate pending segments and polish them end-to-end |
| `prescan_book.py` | per-book prescan CLI: parallel digests, style/synopsis/bible |
| `book_state.py` | series root, book sub-work and glossary master operations |
| `segment_state.py` | source parsing, chapter/segment state, Markdown rendering |
| `token_budget.py` | shared deterministic token estimator |
| `model_api.py` | local OpenAI-compatible model endpoint |
| `profile_rules.py` | resolve the active profile and compose stage rules |
| `preflight_model.py` | probe endpoint concurrency and recommend a worker count |
| `model_client.py` | callable/object client adapter |
| `batch_translate.py` | translation batch protocol |
| `polish_batch.py` | polish batch protocol |
| `preview_migration.py` | migrate a legacy chunk preview into chapter/segment state |
| `preview_pipeline.py` | run preview migration, retranslation and polish end-to-end |
| `review_preview.py` | validate preview review, record false positives and Book 1 GO/NO-GO |
| `review_book_v2.py` | whole-book review runner (blocks, evidence, reports, `--limit-blocks`) |
| `fix_review_issues.py` | apply confirmed review suggestions to segment targets, resumable |
| `book_delivery.py` | render full Markdown + HTML; split Markdown into balanced `Part N` files |
| `review_blocks.py` | plan review blocks, JSON repair, recursive bisection, traces, call budget |
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
