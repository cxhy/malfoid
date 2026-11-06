# Pipeline v2: series/book structure, paragraph state, structured review

Status: ready-for-agent

## Problem Statement

The current translation pipeline treats a Markdown **块** as the unit of state. That causes four concrete failures:

- **校对错位**：proofreading splits the English source and the Chinese translation independently by paragraph count. When light polishing merges or splits a paragraph, the two sides no longer line up, and the report fills with false positives. The two-block preview exposed this: English had 36 paragraphs and Chinese 35, and the report claimed lines were missing that were present in both.
- **复用绑定块边界**：translations, polish and reports are reused only when the whole chunk hash matches. Re-chunking after a source update or a chunk-size change throws away work, and the incremental updater hardcodes a chunk size that does not match the original.
- **审查切分越切越小**：proofreading escalates 4 → 8 → 16 → 32 → 36 splits when a block produces no verdict. Tiny parts lose context, the model invents issues, and a single 6k-character chunk can take 30–40 minutes.
- **成本与范围错配**：a 97k-character preview segment triggered a whole-book prescan that took 40 minutes, produced artifacts keyed to only one chapter, and will be invalidated as soon as the rest of the book arrives.

The workspace also has no series/book structure: each segment became its own flat work, so cross-book terms drift and a one-segment directory cannot grow into a book.

## Solution

Introduce a v2 pipeline beside the existing scripts, leaving completed works untouched.

- A **系列** root holds series metadata, a series glossary master and a series-level story bible. Each **书** is a sub-work and the unit of translation execution.
- **段落状态** replaces chunk-level state: each chapter is a JSON document of **段**, each with a stable index, source hash and target. Markdown becomes a generated delivery layer.
- Translation and polish use an equal-length JSON array protocol: N source segments must produce N targets. Protocol errors retry, then fall back to per-segment calls; no segment is silently dropped.
- **预扫描** runs only after the whole book source is present, with per-chapter parallelism, and merges into the series bible after each book.
- **审查** packs contiguous segments into fixed token-budget blocks, runs in parallel, validates structured JSON output, verifies candidates with bounded evidence, arbitrates conflicts, and stays advisory.
- Migration reuses existing translations by source hash; unmatched segments are retranslated.

The order is A (structure) → B (alignment) → C (review). D (incremental update) is deferred.

## User Stories

1. As a translator, I want one series root with one sub-work per book, so that series-level terms and setting notes stay connected while each book is translated independently.
2. As a translator, I want series metadata to list books in reading order, so that I can see the series structure and progress without opening every book directory.
3. As a translator, I want a series glossary master and a per-book working copy, so that cross-book names stay fixed while new book-specific terms can be added safely.
4. As a translator, I want each book to keep its own source, work state and translation output, so that finishing Book 1 does not disturb Book 2.
5. As a translator, I want plain-text and Markdown sources split into chapters and paragraphs automatically, so that Wattpad exports enter the pipeline without hand-editing.
6. As a translator, I want chapter titles represented as translatable segments, so that headings are translated with the same rules as body text.
7. As a translator, I want each paragraph to carry a stable identity across re-chunking and rebatching, so that saved work is not tied to a transient chunk boundary.
8. As a translator, I want inline formatting inside a paragraph preserved, so that italics and emphasis survive the JSON round trip.
9. As a translator, I want Markdown regenerated from segment state, so that the delivery layer stays consistent with the state.
10. As a translator, I want a round-trip check between segment state and Markdown, so that formatting or paragraph loss is caught before translation.
11. As a translator, I want long paragraphs split at sentence boundaries with continuation markers, so that oversized source text fits the model budget without breaking the paragraph identity.
12. As a translator, I want continuation parts merged back into one paragraph on export, so that the reader sees the original paragraph structure.
13. As a translator, I want batches packed by token budget rather than character count, so that batch size tracks the model's actual limit.
14. As a translator, I want translation to return exactly one target per source segment, so that no paragraph is skipped or duplicated.
15. As a translator, I want a count/complete/non-empty validation on model output, so that a malformed batch is detected before it touches state.
16. As a translator, I want protocol failures retried a bounded number of times, so that transient malformed JSON does not force a manual rerun.
17. As a translator, I want a per-segment fallback after protocol retries, so that one bad batch cannot lose the whole batch.
18. As a translator, I want polish to follow the same equal-length contract, so that polishing cannot change paragraph count or drop content.
19. As a translator, I want the original translation preserved, so that a failed polish can be discarded without losing work.
20. As a translator, I want existing two-block translations reused by source hash, so that the preview work survives the migration.
21. As a translator, I want unmatched migrated segments retranslated, so that migration never silently reuses the wrong text.
22. As a translator, I want prescan to wait until the whole book source is present, so that style, synopsis and story bible are built from the complete book rather than one segment.
23. As a translator, I want prescan chapter digests run in parallel with a bounded worker count, so that a long book does not spend an hour on serial digest calls.
24. As a translator, I want prescan artifacts cached by source hash, so that unchanged chapters are not reprocessed.
25. As a translator, I want a series-level story bible updated after each book, so that later books inherit earlier canon divergences and character states.
26. As a translator, I want review blocks packed from contiguous segments with a fixed token budget, so that review calls have enough context and stable boundaries.
27. As a translator, I want review blocks to run in parallel with a bounded worker count, so that review time scales with blocks rather than serial rounds.
28. As a translator, I want review output as structured JSON with issue count and completeness, so that malformed or truncated reviews are rejected instead of merged.
29. As a translator, I want review blocks to bisect only when structured output is invalid, so that splitting does not become an exponential ladder.
30. As a translator, I want per-segment retry limits inside review, so that one stubborn paragraph does not stall the whole block.
31. As a translator, I want review candidates verified against bounded cross-chapter evidence, so that term and pronoun drift is confirmed before it is reported.
32. As a translator, I want contradictory proposals across blocks arbitrated, so that the report does not contain mutually exclusive fixes.
33. As a translator, I want review to stay advisory, so that no model edits enter the formal text without human or explicit fix-step control.
34. As a translator, I want review reports as structured JSON plus a human-readable summary, so that both machines and I can read the result.
35. As a translator, I want a PASS/FAIL summary per block, so that I can see review status at a glance.
36. As a translator, I want legacy proofread reports kept for completed works, so that old audits remain readable.
37. As a translator, I want old tools and old works untouched, so that adopting v2 does not break finished books.
38. As a translator, I want the v2 tools to address a book sub-work directly, so that commands remain simple and inspectable.
39. As a translator, I want the local model budget rules preserved (reasoning counts toward completion), so that v2 does not reintroduce overflow failures.
40. As a translator, I want the two-block preview used to validate review before Book 1 mass translation, so that review architecture problems surface cheaply.
41. As a translator, I want each book delivered as one HTML and one Markdown file, so that a single-year book does not need volume splitting.
42. As a maintainer, I want the new modules tested offline with injected model clients, so that tests are fast, deterministic and independent of the local endpoint.
43. As a maintainer, I want tests at public module seams rather than internal helpers, so that refactoring does not break tests that still describe behavior.
44. As a maintainer, I want the migration path documented, so that the existing preview and future books can be moved without guesswork.

## Implementation Decisions

**Series and book addressing**
- A series root holds series metadata, a series glossary master and a series-level story bible.
- Books live as sub-works in reading order; each book keeps metadata, source, work state and translation output.
- Translation commands address a book sub-work directly; series-level commands address the series root.
- Legacy flat works remain valid and are not migrated.

**Source ingestion and segment state**
- A new book-state module converts plain text and Markdown into chapters and segments.
- Each chapter is one JSON document. Each segment record carries: index, kind (heading or text), source text, source hash, and target (nullable).
- Segment identity is chapter + index + source hash; chunk or batch boundaries do not affect it.
- Chapter titles are heading segments and are translated.
- Inline Markdown stays inside the segment text; no separate inline model.
- Markdown is rendered from segment state for delivery and compatibility.
- A round-trip check verifies that rendering segment state reproduces the expected chapter structure.
- Segments larger than the per-segment token limit are split at sentence boundaries; continuation parts are marked and merged back on render.

**Batch protocol**
- Batches are packed by token budget (target range roughly 1800–2200 tokens per batch, with a lower per-segment ceiling).
- Translation and polish request a JSON array of exactly N targets for N source segments.
- Validation checks array length, a completeness flag, and non-empty content.
- Protocol errors retry up to two additional attempts.
- After retries, the batch falls back to one call per segment; failures are recorded, never silently dropped.
- Results are written back to segment state by segment identity.

**Prescan**
- Prescan runs only when the whole book source is present.
- Chapter digests run in parallel with a configurable worker count (default 2).
- Digest artifacts are cached by source hash.
- Style brief, synopsis and book story bible are built from the complete book.
- After a book completes, its story bible is merged into the series-level bible.
- Preview translation skips prescan and uses glossary plus neighbour context.

**Review**
- Review blocks pack contiguous segments to roughly three translation batches of tokens.
- Blocks run in parallel with a configurable worker count (default 2).
- Review output is structured JSON: issues, reviewed segment count, and a completeness flag.
- Invalid output triggers bisection; per-segment retries are bounded.
- Candidate issues are verified against bounded cross-chapter evidence, up to two rounds.
- Contradictory proposals are grouped and arbitrated; unresolved conflicts are left for human decision.
- Review stays advisory: it writes reports and recommendations, not formal text changes.
- Review artifacts are structured JSON plus a human-readable summary per block, with a PASS/FAIL summary.
- Legacy proofread artifacts remain for old works.

**Migration and compatibility**
- Existing preview translations are re-segmented and matched by source hash; matched segments reuse their translations, unmatched segments are retranslated.
- New v2 modules live beside the old scripts; old commands and old works keep working.
- Delivery for a single-year book is one HTML and one Markdown file; volume splitting remains available for multi-year works.

**Model budget**
- The existing local model budget rules stay: reasoning tokens count toward completion, low reasoning by default, and overflow handling prefers splitting before dropping reasoning effort.

## Testing Decisions

A good test verifies external behavior through a public seam, uses an independent expected value, and survives internal refactoring. Tests are offline: no network, no live model endpoint. Model calls are replaced by an injected fake client.

**Seam 1: book state**
- Test `resolve_book` with a synthetic series root: series metadata resolves to the correct book sub-work.
- Test `read_source` on a small plain-text fixture: expected chapters, expected paragraph boundaries, titles as heading segments.
- Test `load_chapter`/`save_chapter`: state survives a write/read round trip.
- Test `render_markdown`: segment state renders back to the expected Markdown, including inline formatting and heading order.
- Test source hash stability: same source gives the same hash; changed source gives a different hash.
- Test long-segment splitting and continuation merging: a synthetic oversized paragraph splits at sentence boundaries and merges back.

**Seam 2: batch protocol**
- Test `plan_batch`: token budget groups segments as expected and never exceeds the batch budget.
- Test `translate_batch` with a fake client returning the wrong count: it retries, then falls back per segment, and every segment ends with a target.
- Test `translate_batch` with a fake client returning incomplete or empty targets: validation rejects the batch and the fallback path runs.
- Test `apply_targets`: targets land on the right segment identities and do not shift when segment order is unchanged.
- Test polish alignment: a fake polisher returning a different count is rejected and the original translation is preserved.

**Seam 3: review blocks**
- Test `plan_blocks`: contiguous segments pack into blocks within the token budget and block boundaries are deterministic.
- Test `validate_review_output`: accepts a complete payload with the right reviewed count; rejects a wrong count, missing completeness flag, or malformed shape.
- Test bisection trigger: invalid output at block level causes a smaller block plan, not an exponential ladder.
- Test `merge_reports`: block results merge in original order and verdicts aggregate deterministically.
- Test `arbitrate`: contradictory proposals group into conflicts; unresolved conflicts remain flagged for human decision.

**Prior art**
- Existing tests use `tmp_path` fixtures, import modules from the tools path, and assert on pure function output.
- Existing overflow tests already check reasoning-budget accounting.
- Existing converter tests use synthetic fixtures and never touch the network.
- New tests should follow the same pattern: synthetic inputs, expected values written independently, no live model calls.

## Out of Scope

- Phase D: paragraph-level incremental update, source-hash-driven reuse, and retiring the hardcoded chunk size. It is designed but deferred.
- Autofix, shadow revision, blind recheck, and automatic publication of review changes. Review stays advisory for now.
- Best-of-three or multi-draft translation.
- Rewriting or migrating completed legacy works.
- Translating Book 1 beyond the existing preview.
- Changing the local model provider or endpoint.
- Web, database, desktop or API surfaces.

## Further Notes

- Phase A+B must be validated on the existing two-block preview before Book 1 mass translation.
- Phase C is validated on the same preview corpus so review false-positive rate can be measured cheaply.
- The preview currently has two translated and polished chunks; migration should reuse them where hashes match.
- The current AGENTS.md describes the target flow; operational commands remain v1 until v2 lands.
- The series root and book sub-work decision is recorded in ADR 0001; paragraph state in ADR 0002; review architecture in ADR 0003.
