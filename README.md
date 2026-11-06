# malfoid

**A local-first, file-based translation pipeline for long-form fiction.**

Malfoid turns AO3 / fanfiction.net / PDF sources into Markdown, translates chunk by
chunk with a glossary-first workflow, polishes the result, and runs an independent
fidelity proofread before assembling reader-facing HTML and Markdown. Every step is a
plain script; every intermediate artifact is a file you can inspect, diff and resume.

[简体中文](#简体中文) · [Pipeline](docs/pipeline.md) · [Tool reference](docs/tools.md) · [Wenyi comparison](docs/wenyi-comparison.md) · [Contributing](CONTRIBUTING.md)

---

## Why another translation pipeline?

| Common approach | Malfoid |
|---|---|
| Whole book in one prompt, no recovery | Chunked, resumable, per-chunk artifacts on disk |
| Glossary added after the fact | Glossary-first: frequency scan → curated terms → every chunk |
| One model does translation and review | Separate contexts for translation, polish and proofread |
| Server, database, desktop app | Plain Python scripts, Markdown/JSON files, any OpenAI-compatible endpoint |
| Quality process hidden in a UI | Reports, checks and diffs you can audit with `git diff` |

Malfoid deliberately keeps the **operational flexibility** of a shell-driven workflow
while borrowing the engineering discipline of larger projects: unit tests, linting,
pre-commit hooks, CI, and a documented artifact contract.

---

## Features

- **Source converters** — AO3 HTML, FicHub (fanfiction.net) HTML and chapter-numbered
  PDFs → chapter-aligned Markdown plus `meta.json`.
- **Glossary-first translation** — term table seeded with official names, frequency
  counting, per-chunk prompt context (glossary + neighbour excerpts).
- **HP fanfic profile** — setting-specific rules for female Draco, official HP names,
  canon divergence, timeline/age, reveal timing, British school address and spells, kept
  in `profiles/hp-fanfic/` and shared by every stage.
- **Resumable whole-book prescan** — chapter digests, style brief, synopsis and an HP
  story bible, content-addressed and cached so reruns only rebuild changed chapters.
- **Whole-book continuity review** — a separate cross-chapter pass for term/pronoun
  drift, timeline and character-status contradictions, reveal-timing errors and
  canon-divergence drift, with bounded evidence and a conflict list.
- **Resumable chunks** — `work_temp/` is the source of truth; reruns skip finished
  chunks and can retry structure failures.
- **Polish pass** — light-touch Chinese prose polish with structural/length/term checks.
- **Independent proofreading** — a separate model context sees only the source, the
  polished chunk and the glossary; reports use a fixed severity structure and a
  PASS/FAIL verdict.
- **Incremental updates** — content-addressed rechunking reuses unchanged chunks when a
  source is revised, archiving the old version instead of deleting it.
- **Volume delivery** — multi-year works split by school year into per-volume HTML and
  Markdown, with an HTML template rather than bare Pandoc defaults.
- **Flexible model wiring** — any OpenAI-compatible chat-completions endpoint via
  `MALFOID_BASE_URL` / `MALFOID_API_KEY` / `MALFOID_MODEL`, with a local model-registry
  fallback.

---

## Quick start

Requirements: Python 3.10+, `beautifulsoup4`, and (for HTML delivery) Pandoc. Calibre is
optional and only needed for EPUB/DOCX/PDF output.

```bash
python -m venv .venv
.venv/bin/pip install beautifulsoup4 requests pypandoc-binary

export MALFOID_BASE_URL="http://127.0.0.1:8000/v1"
export MALFOID_API_KEY="your-key"
export MALFOID_MODEL="your-model"
```

Convert a source and start a work:

```bash
.venv/bin/python tools/ao3_convert.py source/My_Work.html
# or: tools/fic_convert.py source/fic.html
# or: tools/pdf_convert.py source/book.pdf --meta-from source/ao3.html --work-id my-work

.venv/bin/python .pi/skills/translate-book/scripts/convert.py \
  works/my-work/source/work.md --olang zh --chunk-size 12000 --temp-root works/my-work
```

Prescan, translate, polish, proofread and review:

```bash
.venv/bin/python tools/build_prescan.py works/my-work --effort low
.venv/bin/python tools/make_prompt_ctx.py works/my-work/work_temp
.venv/bin/python tools/translate_all.py works/my-work/work_temp --workers 1 --effort low
.venv/bin/python tools/polish_all.py works/my-work --effort minimal
.venv/bin/python tools/proofread_all.py works/my-work --effort low --workers 1
.venv/bin/python tools/review_book.py works/my-work --effort low
.venv/bin/python tools/merge_polished.py works/my-work
.venv/bin/python tools/build_html.py works/my-work
.venv/bin/python tools/finalize.py works/my-work
```

The full command reference, artifact contract and troubleshooting notes live in
[docs/pipeline.md](docs/pipeline.md).

---

## Repository layout

```text
tools/                 converters, pipeline drivers, QA and delivery scripts
profiles/              translation profiles (HP fanfic rules, prescan and review prompts)
tests/                 offline unit tests (no network, no model calls)
docs/                  pipeline contract and tool reference
.pi/skills/            vendored agent skills used by the pipeline (MIT, see notices)
pyproject.toml         lint/test configuration
.github/workflows/     CI: ruff + pytest on Python 3.11 and 3.13
```

**Content is never committed.** `works/`, source folders, input books (`*.pdf`, `*.epub`,
`*.docx`) and the local agent settings are git-ignored. The repository ships tooling and
documentation only.

---

## Development

```bash
ruff check tools tests
ruff format --check tools tests
pytest
pre-commit install   # optional, runs ruff on commit
```

Tests are offline by design. Anything that needs a model endpoint is marked `network`
and is not part of the default suite.

---

## Design principles

1. **Files are the interface.** No database, no daemon, no hidden state. A work directory
   can be copied, archived and diffed.
2. **One job per context.** Translation, polishing and proofreading get separate model
   contexts so quality checks do not inherit the translator's assumptions.
3. **Evidence over vibes.** Structural checks, term checks, residue checks and
   proofread reports produce reviewable artifacts.
4. **Flexible wiring, strict contracts.** Any OpenAI-compatible endpoint works; the
   artifact layout and report formats stay fixed.
5. **Content stays private.** Source texts, translations and derived books are ignored
   by Git by default.

---

## License

MIT. Vendored skills retain their upstream MIT licenses; see
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

---

## 简体中文

**malfoid** 是一套本地优先、以文件为接口的长篇同人翻译流水线。它把 AO3 /
fanfiction.net / PDF 原文转成 Markdown，按块翻译、润色，再由独立上下文做忠实度
校对，最后按卷生成 HTML 与 Markdown。

特点：

- 术语表先行，逐块注入术语与前后文；
- 所有中间产物都是文件，可断点续翻、可 diff、可审计；
- 翻译、润色、校对各自独立上下文；
- 原文更新走内容寻址的增量翻译，旧版本归档不删除；
- 任何 OpenAI 兼容端点都能接，不绑定特定服务；
- 译文与原文永不进 Git，仓库只放工具、文档和测试。

开发：

```bash
ruff check tools tests
ruff format --check tools tests
pytest
```

详细流程见 [docs/pipeline.md](docs/pipeline.md)。
