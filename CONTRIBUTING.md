# Contributing to malfoid

Thanks for helping improve the pipeline. The project optimizes for **auditable,
resumable, file-based translation** — not for a hosted product.

## What to contribute

- **Converters**: AO3, FicHub and PDF parsing robustness, new source formats.
- **Pipeline**: chunking, glossary handling, prompt context, resume logic, QA checks.
- **Delivery**: HTML/Markdown assembly, volume splitting, incremental updates.
- **Tests**: offline regression cases for parsers, checks and bookkeeping helpers.
- **Docs**: command references, artifact contracts, troubleshooting.

Changes to prompts or quality thresholds affect translation output. Describe the
before/after effect and include the checks you ran (`ruff`, `pytest`, and any pipeline
commands). Prefer small PRs with one behavioral change each.

## Ground rules

1. **Never commit content.** Source books, `works/`, translations, reports and exported
   books are ignored on purpose. Test fixtures must be synthetic or public domain, and
   small.
2. **Keep tests offline.** Anything needing a model endpoint or network access gets the
   `network` marker and stays out of the default suite.
3. **Preserve the artifact contract.** Directory layout, chunk naming, `meta.json`,
   `glossary.json` and proofread report structure are relied upon by other scripts.
4. **Flexible wiring.** Model access goes through `MALFOID_BASE_URL` /
   `MALFOID_API_KEY` / `MALFOID_MODEL`; do not hard-code a vendor.
5. **English for code, Chinese for prose.** Comments, docstrings and CLI messages are
   English; generated translation prose follows the target language.

## Development workflow

```bash
python -m venv .venv
.venv/bin/pip install beautifulsoup4 json-repair pypandoc-binary pytest ruff pre-commit

ruff check tools tests
ruff format --check tools tests
pytest
pre-commit install
```

`main` is the only long-lived branch. Work on short-lived `feat/*`, `fix/*`, `docs/*` or
`chore/*` branches and open a PR against `main`. Keep the tree green: lint, format and
tests must pass before review.

## Commit messages

Use a short imperative subject, optionally with a scope:

```text
fix(pdf): rebuild paragraphs across page breaks
feat(proofread): record severity counts in the summary
docs: document the incremental-update archive layout
```

## Reporting issues

Include:

- the command you ran and its exit status;
- the relevant `work_temp` or report snippet (redact copyrighted source text);
- Python version and whether the issue reproduces offline.
