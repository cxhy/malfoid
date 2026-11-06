# Malfoid vs. Wenyi: feature comparison and adopted changes

This document compares the current `malfoid` translation workspace with the `wenyi`
project (`/home/guodz/wenyi/wenyi`), then records what was adopted and what was
deliberately left out. The goal is not to turn malfoid into a product; it is to make
malfoid a **Harry Potter fanfiction translation workbench** with Wenyi's strongest
quality mechanisms, while keeping the file-based, script-driven workflow.

## 1. Two different kinds of system

| | Malfoid | Wenyi |
|---|---|---|
| Product shape | A workspace of standalone Python scripts and files | A full translation product: core library, CLI, Web API/worker, Tauri desktop |
| State | The work directory is the state (`work_temp/`, `translation/`, JSON/Markdown) | CLI files + SQLite; Web PostgreSQL/Redis/Arq; Desktop SQLite; explicit storage interfaces |
| Input | AO3 HTML, FicHub HTML, chapter-numbered PDF | EPUB, DOCX, PDF (MinerU / BabelDOC), TXT, SRT, FB2, HTML |
| Output | Markdown + templated HTML, per-volume | HTML, EPUB (template-preserving), DOCX, PDF, bilingual editions, SRT |
| Model wiring | Direct OpenAI-compatible HTTP; env override; local pi model registry fallback | Provider registry, per-operation routing, retry layer, usage ledger |
| Extensibility | Edit a script / profile file | Domain services, storage adapters, API contract, UI |
| Tests | Small offline unit suite | Large suite across core/CLI/backend/API/desktop, architecture boundaries |
| Scope | One translator's fanfic workflow | General long-form translation for many users and languages |

Malfoid's advantage is **operational transparency**: every intermediate artifact is a
file, every stage is inspectable, and a rerun is a shell command. Wenyi's advantage is
**quality engineering**: whole-book understanding, resumable book state, evidence-based
review, conflict arbitration, and a much more rigorous review protocol.

## 2. Pipeline comparison

### 2.1 Prescan / book understanding

| | Malfoid before this change | Wenyi | Malfoid now |
|---|---|---|---|
| Chapter digest | Optional, one digest per chapter, no cache | Required for nonempty chapters, content-addressed, resumable | Resumable, keyed by source hash + prompt/profile version |
| Whole-book synopsis | Optional, built from digests | Built from digests + style analysis + glossary; cached by input fingerprint | Cached by digest/style/profile fingerprint |
| Style analysis | Style brief from samples + digests | Dedicated analyzer: genre, tone, narration, pacing, register, dialogue, rhetoric, character voices | Same, HP-specialised |
| Book-level reference | None | Style brief + synopsis injected as a stable prefix | New `story_bible.md`: canon divergence, character registry, timeline, secrets/reveals, status |
| Cache identity | None | `source_sha256`, digest version, policy fingerprint | `source_sha256`, prescan version, profile version, input hashes |
| Resume behavior | Exits if `prescan/` exists unless `--force` | Reuses verified artifacts, regenerates only missing/changed | Reuses verified artifacts; `--reuse-digests` migrates legacy digests |

### 2.2 Context construction

| | Malfoid before | Wenyi | Malfoid now |
|---|---|---|---|
| Style | Style brief (optional) | Style guide prefix | Style brief |
| Plot | Book synopsis + chapter digest | Book synopsis + chapter digest | Book synopsis + chapter digest |
| Book-level facts | None | Synopsis, glossary, analysis | HP story bible |
| Glossary | Chunk-relevant subset | Chunk-relevant subset | Same |
| Rolling context | Recent translated paragraphs, char-bounded | Recent translated paragraphs, token-bounded, contiguous suffix | Same (char-bounded) |
| Lookahead | First paragraph of next chunk | One following source paragraph | Same |
| Neighbours | 250–400 char excerpts | Evidence queries / annotations | Same |
| Annotations | None | Paragraph-specific annotation references | Not adopted |

### 2.3 Translation protocol

| | Malfoid | Wenyi |
|---|---|---|
| Unit | Whole Markdown chunk | Numbered paragraph array, one output per paragraph |
| Output contract | Markdown body | JSON `{"translations": [...]}` |
| Paragraph integrity | Post-hoc structural checks | Enforced by array length/order |
| Batch boundary | Chunk boundary, no mid-sentence handling | Explicit unfinished-syntax rule at batch boundaries |
| Precision | None | Optional multi-draft independent translation + synthesis |
| Glossary extraction | Human-curated JSON | Real-time extraction, evidence, conflicts, resolution |
| Resume | Skip chunks with output files | Batch checkpoints + chapter status |

**Adopted now:** profile rules, story bible, lookahead/rolling context, resumable prescan,
whole-book review. **Not adopted yet:** JSON paragraph protocol, precision drafts,
automatic glossary extraction.

### 2.4 Polish

| | Malfoid | Wenyi |
|---|---|---|
| Unit | Whole chunk | Numbered paragraph array |
| Context | Glossary only | Style, glossary, next source; can continue the translation conversation |
| Structural checks | Headings, blocks, length ratio, terms, forbidden names, English residue | Paragraph count/order contract |
| Intensity | light / refine / rewrite; delivery fixed to light | Single polishing stage |

Malfoid keeps its light-edit delivery policy (faithful, no added detail). It now composes
the HP profile into polish rules, so setting-specific constraints are not duplicated.

### 2.5 Review / proofreading

| | Malfoid before | Wenyi | Malfoid now |
|---|---|---|---|
| Unit | One chunk | Whole book, parallel blocks | One chunk + book-level aggregation |
| Context | Source, polished text, glossary, neighbours | Whole-book evidence index: glossary, term occurrences, segment context, book context | Story bible, digest, glossary, bounded cross-chapter occurrence evidence |
| Evidence | None | Agent loop requests evidence before deciding | Bounded evidence supplied up front |
| Candidates | None | Preliminary issues verified/dismissed | Program candidates (term drift, structure, forbidden names) verified/dismissed |
| Consistency | None | Cross-block term/pronoun/fixed-expression conflicts, arbitration | Conflict grouping across chunks |
| Revision | Report only | Shadow revisions, fixer, blind rechecks, autofix publication | Report only |
| Output | Per-chunk report + PASS/FAIL | Review run directory + autofix index | Per-chunk report + book summary + conflicts JSON |

**Adopted now:** cross-chapter review, evidence, candidate verification, conflict list.
**Not adopted:** multi-round evidence agent loop, shadow revision/fixer/autofix. Malfoid
keeps review advisory and leaves edits to the existing proofread-fix-check loop, which is
easier to audit with `git diff`.

### 2.6 Delivery

| | Malfoid | Wenyi |
|---|---|---|
| Markdown | Yes | Text writer |
| HTML | Templated HTML | HTML writer, bilingual support |
| EPUB | Optional (Calibre), not installed | Template-preserving EPUB |
| DOCX/PDF | No | Yes |
| Volumes | School-year volumes, one HTML+MD per volume | Book-level output |
| Synopsis | Cover/illustration synopsis per volume | — |

Malfoid's volume delivery is already better suited to multi-year HP fanfic than Wenyi's
book-level export. This was kept unchanged.

## 3. What was adopted

### 3.1 `profiles/hp-fanfic/` — one source of truth for HP rules

Previously HP rules were copy-pasted into `translate_chunk.py`, `polish_chunk.py` and
`proofread_chunk.py`, with drift risk. Now:

```
profiles/hp-fanfic/
├── manifest.json          profile id, version, file map, forbidden names, Draco contract
├── translation.md         official names, female Draco, canon divergence, timeline, reveals, spells
├── polish.md              setting-preserving light polish, character voice, intensity
├── proofread.md           HP-specific fidelity checks
├── prescan_digest.md      HP chapter digest sections
├── story_bible.md         HP story bible sections
└── book_review.md         cross-chapter continuity review rules
```

`tools/prompt_profile.py` resolves the profile (`MALFOID_PROFILE` → `meta.json` →
`hp-fanfic`) and composes the stage rules. All model tools use it.

### 3.2 Resumable prescan + HP story bible

`tools/build_prescan.py` now mirrors Wenyi's book-understanding cache identity:

- chapter digest: `source_sha256` + prescan version + profile version;
- style brief: digest set + samples + profile version;
- synopsis: digest set + profile version;
- story bible: digest set + style brief + glossary + profile version;
- content-addressed digest cache under `prescan/cache/`;
- rerun reuses verified artifacts; `--force` regenerates; `--reuse-digests` migrates
  legacy digests without re-calling the model.

The new `story_bible.md` is HP-specific and covers the things that actually break fanfic
translations across chapters: canon divergence/AU facts, character registry (gender, age,
house, blood status, forms of address), relationships, timeline, magic/terms, secrets and
who-knows-what, character status, whole-book arc, tone/rating warnings.

### 3.3 Context injection

`tools/make_prompt_ctx.py` now injects, in order: style brief → story bible → synopsis →
chapter digest → glossary → rolling translated context → next-chunk lookahead → neighbour
source. This is the Wenyi context order, with the HP story bible added between style and
plot.

### 3.4 Whole-book continuity review

`tools/review_book.py` adds the cross-chapter layer Wenyi has and malfoid lacked:

- program candidates: paragraph-count drift, forbidden names, bare male-form Draco, term
  drift;
- model review with story bible, chapter digest, relevant glossary and bounded
  cross-chapter occurrence evidence;
- candidate verification (model-confirmed issues survive; unconfirmed term drift is
  dropped; definite program checks survive even if the model fails);
- per-chunk reports, book summary, conflict list for human arbitration.

This complements, rather than replaces, `proofread_chunk.py`: proofread handles local
fidelity, review handles book-level continuity and drift.

### 3.5 Tests and docs

Offline tests cover profile loading, prescan helpers, deterministic review checks,
conflict grouping and report merging. `docs/pipeline.md`, `docs/tools.md`, `README.md`
and `AGENTS.md` now document the prescan, story bible, profile and continuity review.

## 4. What was deliberately not adopted

| Wenyi feature | Why not (yet) |
|---|---|
| Web/Desktop/API/DB architecture | Malfoid is deliberately file-based and script-driven; adding a service layer would hide artifacts and add operational weight |
| Automatic glossary extraction / evidence store | The project uses a human-curated glossary; automatic extraction risks conflicting with official names and the Draco contract |
| Multi-round evidence agent loop | Too much complexity and model budget for the current local model; bounded evidence + candidate verification captures most of the benefit |
| Shadow revision + autofix publication | Malfoid's proofread → minimal fix → re-check → fresh proofread loop is already auditable; autofix would blur authorship and make diffs harder to review |
| JSON paragraph protocol | A larger refactor of the existing Markdown chunk pipeline; current structural checks catch most damage. This is the highest-value future change |
| Precision multi-draft | Useful for critical passages, but expensive; could be added per-chunk later |
| EPUB/DOCX/PDF | Calibre is not installed; HTML+Markdown is the delivery contract |
| Bilingual editions | Not requested for this project |

## 5. Remaining gaps and suggested next steps

1. **Paragraph-level JSON protocol.** Translate/polish numbered paragraphs and validate
   array length/order. This is the biggest remaining robustness gap versus Wenyi, and
   would eliminate merged/split paragraph damage at the source.
2. **Evidence agent loop.** Add bounded on-demand evidence queries for review, rather
   than supplying all evidence up front. Useful for very long books.
3. **Precision mode.** Optional independent drafts + synthesis for chapters where names,
   dates, spells or reveal timing are dense.
4. **Automatic glossary candidate extraction.** Not to replace the curated table, but to
   suggest new fanfic-specific names/aliases for human review.
5. **Review resume/cache.** The review currently reuses existing reports by filename;
   Wenyi keys reuse to content/config/glossary fingerprints. Add hashes so a changed
   chunk or glossary invalidates only affected reports.
6. **Usage ledger.** Wenyi records per-operation usage. Malfoid prints usage per call;
   a per-work JSON ledger would help cost tracking.

## 6. HP fanfic specialisation summary

Malfoid is now specialised around the failure modes of HP fanfiction translation:

- **Female Draco contract** (德拉科妮娅 / 德拉 / 德拉科妮娅·马尔福, 她, no 达西).
- **Official HP terminology** (mainland People's Literature names; no Taiwan names).
- **Canon divergence** (AU facts are preserved, not "corrected" to canon).
- **Timeline and age** (school year, dates, event order).
- **Secrets and reveal timing** (who knows what, and when).
- **Character status** (alive/dead, injured, house, blood status, allegiance).
- **Forms of address** (British school register, nicknames, honorifics).
- **Spells and magic terms** (official Chinese names; invented spells consistent).
- **Voice and intensity** (character-specific register; slurs and threats not softened).

The glossary remains human-curated, as before; the new layer supplies the book-level
facts and cross-chapter checks that a glossary alone cannot provide.
