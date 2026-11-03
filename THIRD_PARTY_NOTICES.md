# Third-party notices

Malfoid vendors three agent skills under `.pi/skills/`. Each retains its upstream MIT
license; the full texts are stored next to each skill.

| Component | Upstream | License |
|---|---|---|
| `translate-book` | deusyu/translate-book | MIT, Copyright (c) 2025 Rainman |
| `humanizer-zh` | ai-zixun/humanizer-zh | MIT, Copyright (c) 2026 aizixun |
| `fiction-humanizer-zh` | deedeekong07-alt/fiction-humanizer-zh | MIT |

The pipeline scripts in `tools/` are original work licensed under the repository's MIT
license.

Runtime dependencies (installed separately, not vendored): BeautifulSoup4, Requests,
Pandoc (via `pypandoc-binary`), and optionally Calibre for EPUB/DOCX/PDF output.
