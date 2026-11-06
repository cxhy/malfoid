# Pipeline contract

Malfoid's pipeline is a sequence of scripts that read and write a **work directory**.
Nothing is stored in a database or daemon: the directory is the state.

> **v2 is the active pipeline.** v1 tools are archived under `archive/v1/tools/`
> and are not used. The v2 section below is the current contract; the v1 stages
> remain as historical reference.

## Work directory layout

A series has one root directory; each book is a sub-work and the unit of execution
(ADR 0001). Legacy single-book works keep the flat layout.

```text
works/<series-id>-<slug>/
├── series.json               series title, author, book list, order, status
├── glossary.json             series glossary master
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
    │   └── manifest.json     chunk metadata
    ├── translation/
    │   ├── draft/            unpolished merged book
    │   ├── polished/         polished text + book.md + book.html
    │   ├── proofread/        per-chunk fidelity reports (legacy)
    │   ├── review/           structured review JSON + human summaries
    │   └── <Title>.md / .html  final reader-facing deliverables
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
inside the live `source/chapters/` directory.

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
python tools/book_delivery.py works/<series>/books/book-01
```

`review_preview.py` plans review blocks over the whole preview, maps global review
indices back to chapter/local positions, records alignment and false-positive
metrics, and writes a Book 1 GO/NO-GO acceptance record. `review_book_v2.py` is the
generic whole-book review runner. Both write only under `translation/review/` and
stay advisory. `book_delivery.py` renders translated segment state to
`translation/<title>.md` and `translation/<title>.html` through `build_html.py`.

## Model configuration

All model-calling tools resolve the endpoint in this order:

1. `MALFOID_BASE_URL` (+ optional `MALFOID_API_KEY`), any OpenAI-compatible
   chat-completions endpoint;
2. `MALFOID_MODEL` overrides the model name where a tool accepts `--model`;
3. fallback: the local pi model registry at `~/.pi/agent/models.json`.

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
