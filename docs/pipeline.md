# Pipeline contract

Malfoid's pipeline is a sequence of scripts that read and write a **work directory**.
Nothing is stored in a database or daemon: the directory is the state.

## Work directory layout

```text
works/<work-id>-<slug>/
├── meta.json                 title, author, tags, rating, chapters, source URL
├── glossary.json             curated term table (human-editable)
├── source/
│   ├── original.html|pdf     the downloaded source
│   └── work.md               chapter-aligned Markdown
├── work_temp/                translation state (the source of truth)
│   ├── chunk0001.md          source chunk
│   ├── output_chunk0001.md   raw translation
│   ├── prompt_ctx/           glossary + neighbour excerpts per chunk
│   └── manifest.json         chunk metadata
├── translation/
│   ├── draft/                unpolished merged book
│   ├── polished/             per-chunk polished text + book.md + book.html
│   ├── proofread/            per-chunk fidelity reports + summary.md
│   └── <Title>.md / .html    final reader-facing deliverables
└── archive/<timestamp>/      previous source version after an incremental update
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

### 3. Translate

```bash
python tools/translate_all.py works/<work>/work_temp --workers 1 --effort low
# single chunk:
python tools/translate_chunk.py works/<work>/work_temp chunk0001 --effort low
```

Each chunk gets its own prompt: glossary table, the chunk text, and read-only excerpts
from neighbouring chunks. Output goes to `work_temp/output_chunkXXXX.md`; the raw
translation is never overwritten by polishing.

### 4. Polish

```bash
python tools/polish_all.py works/<work> --effort minimal
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

## Model configuration

All model-calling tools resolve the endpoint in this order:

1. `MALFOID_BASE_URL` (+ optional `MALFOID_API_KEY`), any OpenAI-compatible
   chat-completions endpoint;
2. `MALFOID_MODEL` overrides the model name where a tool accepts `--model`;
3. fallback: the local pi model registry at `~/.pi/agent/models.json`.

This keeps the pipeline portable while preserving the original local-model workflow.

## Reasoning budgets

Reasoning tokens count toward `max_tokens`. For polishing use `minimal`; `medium` or
`high` can consume the whole budget and return empty content. For translation and
proofreading, `low` is the usual setting. When a chunk overflows, split it rather than
raising the budget indefinitely.
