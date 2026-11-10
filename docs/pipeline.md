# Pipeline contract

Malfoid's pipeline is a sequence of scripts that read and write a **work directory**.
Nothing is stored in a database or daemon: the directory is the state.

> **v2 is the active pipeline.** v1 tools are archived under `archive/v1/tools/`
> and are not used. The v2 section below is the current contract; the v1 stages
> remain as historical reference.

## Work directory layout

A series has one root directory; each book is a sub-work and the unit of execution
(ADR 0001). Legacy single-book works keep the flat layout. The workspace global
glossary lives at `glossary/hp.json` (tracked): canonical HP terms and the
project-wide gender-swapped Malfoy rule only, never series-specific or AU terms.

```text
glossary/hp.json                global canonical glossary (tracked)
works/<series-id>-<slug>/
├── series.json               series title, author, book list, order, status
├── glossary.json             series master (global baseline + series terms/overrides)
├── story_bible.md            series-level reference
└── books/book-NN/            one book per sub-work
    ├── meta.json             title, author, tags, rating, chapters, source URL
    ├── source/
    │   ├── original.html|pdf the downloaded source
    │   └── work.md           chapter-aligned Markdown (v1)
    │   └── chapters/ch-NN.json  segment state (v2)
    ├── work_temp/            translation state (the source of truth)
    │   ├── chunk0001.md      source chunk (v1)
    │   ├── output_chunk0001.md  raw translation (v1)
    │   ├── prompt_ctx/       glossary + neighbour excerpts per chunk
    │   ├── manifest.json     chunk metadata
    │   ├── progress.jsonl    per-chapter translation progress
    │   ├── usage.jsonl       per-call model usage and duration
    │   ├── review_cache/     complete review blocks keyed by content + rules
    │   ├── review_traces/    raw responses from rejected review calls
    │   └── fix_review/       fix log + idempotency state
    ├── translation/
    │   ├── draft/            unpolished merged book
    │   ├── polished/         polished text + book.md + book.html
    │   ├── proofread/        per-chunk fidelity reports (legacy)
    │   ├── review/           structured review JSON + human summaries
    │   ├── <Title>.md / .html  final full Markdown + single HTML
    │   └── <Title> - Part N.md  balanced Markdown parts (when over the limit)
    └── archive/<timestamp>/  previous source version after an incremental update

works/<work-id>-<slug>/       legacy single-book work (unchanged)
```

Content directories (`works/`, source folders) are git-ignored. Only tooling lives in
the repository.

## Stages

### 1. Convert the source

| Source | Tool | Notes |
|---|---|---|
| AO3 HTML export | `tools/ao3_convert.py` | chapter divs, preface metadata |
| FicHub HTML export | `tools/fic_convert.py` | `div#chap_N` structure |
| Chapter-numbered PDF | `tools/pdf_convert.py` | rebuilds paragraphs from indentation |

All converters write `source/work.md` and `meta.json`.

### 2. Chunk and glossary

```bash
python .pi/skills/translate-book/scripts/convert.py \
  works/<work>/source/work.md --olang zh --chunk-size 12000 --temp-root works/<work>
python .pi/skills/translate-book/scripts/glossary.py count-frequencies works/<work>/work_temp
python tools/make_prompt_ctx.py works/<work>/work_temp
```

`work_temp/glossary.json` holds terms as `{"source", "target", "aliases", "notes"}`.
Update it as new names appear, then rerun frequency counting and prompt-context
generation so later chunks see the updated table.

### 2b. Whole-book prescan (HP fanfic story bible)

```bash
python tools/build_prescan.py works/<work> --effort low
# reuse existing chapter digests without calling the model:
python tools/build_prescan.py works/<work> --reuse-digests
```

The prescan is the Wenyi-style book-understanding layer, specialised for Harry Potter
fanfiction. It writes under `work_temp/prescan/`:

| Artifact | Purpose |
|---|---|
| `chapter_digests/ch-NN.md` | per-chapter digest: plot, people, timeline, status, secrets, magic, voice, foreshadowing |
| `style_brief.md` | narrative voice and character voices |
| `book_synopsis.md` | translation-only whole-book arc and future facts |
| `story_bible.md` | HP-fanfic reference: canon divergence, character registry, timeline, secrets/reveals, status |
| `chunk_map.json` | chunk → chapter mapping |
| `meta.json` | source hashes, profile version, model usage, cache identity |

Digests are content-addressed (`cache/digest-<sha>.md`). A rerun reuses verified digests
and only calls the model for changed chapters or changed inputs; `--force` regenerates
everything.

### 3. Translate

```bash
python tools/translate_all.py works/<work>/work_temp --workers 1 --effort low
# single chunk:
python tools/translate_chunk.py works/<work>/work_temp chunk0001 --effort low
```

Each chunk gets its own prompt: the HP profile rules, glossary table, style brief, story
bible, book synopsis, chapter digest, recent translated context, next-chunk lookahead and
read-only neighbour excerpts. Output goes to `work_temp/output_chunkXXXX.md`; the raw
translation is never overwritten by polishing.

### 4. Polish

```bash
python tools/polish_all.py works/<work> --effort minimal
# validate existing polished chunks and retry structurally broken ones:
python tools/polish_all.py works/<work> --recheck --effort minimal
```

Polishing writes `translation/polished/chunkXXXX.md` and runs structural checks:
heading count, block count, Chinese-length ratio, glossary terms, forbidden names and
English residue. `--split 2/3/4` retries oversized chunks in smaller pieces.

### 5. Proofread

```bash
python tools/proofread_all.py works/<work> --workers 1 --effort low
```

Proofreading uses a **fresh context** per chunk: English source, polished Chinese and
the glossary only. Reports go to `translation/proofread/chunkXXXX.md` with a fixed
structure:

```markdown
## 严重（影响情节、人物、因果）
## 一般（信息点偏差、术语、语气强度）
## 轻微（标点、指代模糊、可读性）
结论：PASS | FAIL
```

Each item names the translated phrase, the source phrase and the minimal fix. FAIL
chunks are repaired with `tools/fix_proofread.py`, rechecked with
`tools/check_polished.py`, then proofread again in a new context.

### 6. Merge and deliver

```bash
python tools/merge_polished.py works/<work>
python tools/build_html.py works/<work>
python tools/finalize.py works/<work>
```

`build_html.py` renders through the bundled web template; do not use bare
`pandoc --standalone`, which loses typography. `finalize.py` copies the HTML and
Markdown deliverables to the top of `translation/` and moves superseded merged files
to `translation/draft/`.

### 7. Multi-year volumes

```bash
python tools/split_volumes.py works/<work>
python tools/finalize.py works/<work>
```

Chapter headings containing a school year (`一年级`… / `Year N`) define volumes.
Interludes attach to the following year; a trailing interlude stays with the previous
year. A work with fewer than two detected years is delivered as a single book.

### 8. Incremental updates

```bash
python tools/incremental_update.py works/<work> new_source.html --dry-run
python tools/incremental_update.py works/<work> new_source.html
```

The update is content-addressed: source chunks are realigned and re-chunked, chunks
whose hash is unchanged reuse their translation, polish and proofread report, and
changed chunks are marked pending. The previous `source/`, `work_temp/`, `meta.json`,
`polished/`, `proofread/` and deliverables are archived under
`works/<work>/archive/<timestamp>/`.

## HP fanfic profile

The active profile lives in `profiles/hp-fanfic/`: a `manifest.json` plus one Markdown
rule file per stage (`translation`, `polish`, `proofread`, `prescan_digest`,
`story_bible`, `book_review`). All model-calling tools compose their prompts from the
profile, so HP-specific rules are defined once instead of copied into every script.

Resolution order: `MALFOID_PROFILE` → `profile` field in `meta.json` → `hp-fanfic`.

The profile fixes the female-Draco naming contract (德拉科妮娅 / 德拉 / 德拉科妮娅·马尔福,
pronoun 她, forbidden 达西), HP official terminology policy, canon-divergence handling,
timeline/age tracking, reveal timing, British school address conventions and spell
conventions.

## Pipeline v2

v2 keeps the same file-based contract but makes **段** the state unit. A book
sub-work stores one JSON document per chapter under `source/chapters/`; Markdown
and HTML are generated delivery layers. Legacy v1 works and tools are untouched.

```text
works/<series>/books/book-01/
├── meta.json
├── source/chapters/ch-01.json      chapter segments (source hash + target)
├── work_temp/glossary.json         book glossary working copy
├── translation/
│   ├── <Title>.md / <Title>.html   v2 delivery
│   └── review/                     structured review reports + summaries
└── archive/<timestamp>/            superseded state and delivery artifacts
```

Stale chapter state is archived under `<book>/archive/<timestamp>/chapters/`, never
inside the live `source/chapters/` directory. Serialized range markers (``Ch 1-4``,
``Ch18-19-20``) also record ``covered_chapters`` in the chapter state and meta
record, so progress statistics and deterministic headings do not need to re-parse
the title.

### Preview migration

Move the existing two-block preview into segment state:

```bash
python tools/preview_pipeline.py works/<legacy-work> --chunks 1 2
python tools/preview_pipeline.py works/<legacy-work> --migrate-only
```

Legacy translations are reused by source hash (polished before draft); unmatched
segments are retranslated through the batch protocol and then polished. Preview
prompts include glossary terms relevant to the batch plus read-only excerpts from
neighbouring legacy chunks. Output is `translation/preview/book.md` and
`translation/preview/book.html`, rendered through the bundled web template.

### Review and delivery

```bash
python tools/review_preview.py works/<legacy-work>/preview_v2
python tools/review_book_v2.py works/<series>/books/book-01
python tools/fix_review_issues.py works/<series>/books/book-01
python tools/book_delivery.py works/<series>/books/book-01
```

Before a full review after changing the review prompt, effort or profile, run a
one-block smoke test: `review_book_v2.py <book> --limit-blocks 1`. It writes only
the first block under `translation/review/smoke/` and exits, so prompt
regressions cost one block instead of a whole-book rerun and the full review
reports stay untouched. When `--workers` is omitted, the review runner probes the
endpoint with `preflight_model.py` and uses the recommended worker count;
`--no-preflight` forces one worker. The fixer reads confirmed issues from `translation/review/`,
applies one target per affected segment, and records applied segments in
`work_temp/fix_review/state.json`; reruns skip already-applied targets and exit
0 with recorded failures unless `--strict` is passed. Failed targets are written
to `work_temp/fix_review/manual_queue.json` and `.md` for human triage. The
fixer validates a corrected target against the source length, not the previous
target, so a correct repair of an already-broken segment is not rejected.
`--dry-run` is plan-only: it lists targets that would be fixed, checks saved
state, and makes no model calls or writes. Before calling the model, the fixer
compares the current target with the target recorded at review time and skips
segments that were edited after review, so manual repairs are not overwritten. It only
auto-fixes omission, addition, mistranslation, semantic reversal, consistency and
terminology issues at high or medium severity; low-severity advisory checks and
style suggestions are left for triage and listed separately. A confirmed issue whose review-time target contains a glossary `forbidden_targets` variant is a deterministic terminology violation and is collected even when marked low severity; other low-severity issues remain skipped.

Translation saves chapter state after every completed batch, so an interrupted
run keeps all finished batches. `--max-seconds N` stops gracefully between
batches and returns `stopped=true`; rerunning the same command resumes from the
saved state. Delivery derives chapter headings deterministically from the
chapter marker (number and range), so model heading drift cannot reach the
reader-facing Markdown.

Glossary resolution is global → series → book. The global canonical table wins
on canonical targets unless a series term sets `override_global`; series-only
terms are added on top. Book-local terms supplement the series master and merge
back with `sync_glossary.py --merge-back`, which never edits the global table.
Translation loads the merged global + series + book glossary; polish injects the
same relevant terms per batch. Review and fix load the same glossary plus
unambiguous English→Chinese rows from the book and series story bibles; the
glossary always wins on conflicts. `sync_glossary.py` refreshes a book copy, and
ingest/prescan also synchronize it. Glossary entries may carry
`forbidden_targets`, which the delivery verifier scans deterministically.

`review_preview.py` plans review blocks over the whole preview, maps global review
indices back to chapter/local positions, records alignment and false-positive
metrics, and writes a Book 1 GO/NO-GO acceptance record. `review_book_v2.py` is the
generic whole-book review runner. Both write only under `translation/review/` and
stay advisory. `book_delivery.py` renders translated segment state to
`translation/<title>.md` and `translation/<title>.html` through `build_html.py`.
Before verification it merges book-local glossary terms into the series master and
synchronizes every book in the series; conflicts with the series/global glossary
block delivery unless `--allow-glossary-conflicts` is passed. The full Markdown
and single HTML are always kept. When the Markdown exceeds
`--split-md-chars` (default 80,000 characters), delivery also writes balanced
chapter-boundary parts as `translation/<title> - Part N.md`; parts never split
inside a chapter, and an oversized chapter raises an error. Use `--no-split-md`
to disable splitting. Before writing, delivery runs `verify_book.py`: missing
targets, forbidden names, English residue and glossary variants are errors that
block delivery; heading-format issues are warnings. `--skip-verify` is only for
debugging.

The v2 review call injects the active profile's proofread and book-review rules
(including the female-Draco contract) into every block prompt, so AU changes are
not "corrected" back to canon. Review covers sentence-level fidelity (omission, addition, mistranslation, semantic reversal, term and spell accuracy) as well as cross-chapter consistency; v2 has no separate per-chunk proofread stage. Evidence verification also receives the canonical glossary, and a `terminology` candidate whose target or candidate text exactly hits a glossary `forbidden_targets` variant is confirmed deterministically without a model call. It uses JSON mode and a tolerant parser that
accepts only boundary-only repairs (fence, missing outer delimiter); structural
JSON repairs are rejected. A rejected block bisects recursively down to single segments, then
retries each segment a bounded number of times. Every block writes a recovery
trace with the raw model response to `work_temp/review_traces/`, and block
results are cached under `work_temp/review_cache/`. Review defaults to effort
`none` because low reasoning stalls large review blocks on the local model; the
per-call timeout is `MALFOID_REVIEW_TIMEOUT` (default 900s). The review call
budget (`--max-calls`, default 128) bounds total calls per block so recursive
bisection cannot become an exponential ladder.

## Model configuration

All model-calling tools resolve the endpoint in this order:

1. `MALFOID_BASE_URL` (+ optional `MALFOID_API_KEY`), any OpenAI-compatible
   chat-completions endpoint;
2. `MALFOID_MODEL` overrides the model name where a tool accepts `--model`;
3. fallback: the local pi model registry at `~/.pi/agent/models.json`.

Other environment variables:

- `MALFOID_TIMEOUT` — per-call timeout for translation/polish (default 900s);
- `MALFOID_REVIEW_TIMEOUT` — per-call timeout for review calls (default 900s);
- `MALFOID_PROFILE` — override the profile id (default `hp-fanfic`).

Run `tools/preflight_model.py` before launching parallel work. It measures one
concurrent pair against two sequential calls and prints the recommended worker
count; the local endpoint is usually serial (`workers=1`).

Default reasoning effort is `low` for translation and polish: quality comes first,
and the local model's reasoning overhead is accepted for book-length work. Prescan
and review default to `none` because their outputs are structural summaries or
JSON checks, and low reasoning stalls large blocks on the local model. Post-review
fixes also default to `low`, where the model has to judge and rewrite a specific
flagged segment.

This keeps the pipeline portable while preserving the original local-model workflow.

## Reasoning budgets and overflow recovery

Reasoning tokens count toward `max_tokens`. The model can spend the entire completion
budget thinking and return empty content (`finish_reason=length`, reasoning tokens at
the cap). The pipeline treats that as an overflow, not a valid empty translation.

Recovery ladder, applied automatically:

1. **Smaller prompt first.** Translation, polish and proofread send only the glossary
   terms that occur in the current text, not the whole book glossary. This cuts prompt
   size and unrelated reasoning.
2. **Split before dropping reasoning.** Translation splits chunks over 6000 characters;
   polish splits long parts and escalates up to the paragraph count (max 16); proofread
   starts at split 4 for chunks over 4000 characters and escalates to one paragraph per
   part; whole-book review escalates splits 1 → 2 → 4 → 8.
3. **Disable reasoning only at the smallest unit.** When a part is a single paragraph
   (or small enough), the tool retries with `reasoning_effort=none`. If that still
   overflows, the tool fails loudly instead of writing an empty artifact.
4. **Prescan digests split recursively.** A chapter part that overflows is split at
   paragraph boundaries, each half is digested, and the digests are merged. Style,
   synopsis and story bible generation retry with reasoning disabled; if still truncated,
   the prescan raises instead of saving a partial artifact.
5. **No broken artifacts count as done.** A failed polish removes the bad polished file;
   `polish_all --recheck` validates existing polished chunks and retries broken ones. A
   proofread report without a PASS/FAIL conclusion is removed so the chunk is retried.

For polishing use `minimal`; `medium` or `high` can consume the whole budget. For
translation and proofreading, `low` is the usual setting. Raise `--max-tokens` only after
the split ladder, not before it.
