# Third-party notices

Malfoid vendors agent skills under `.pi/skills/`. Each retains its upstream MIT
license; the full texts are stored next to each skill.

| Component | Upstream | License |
|---|---|---|
| `translate-book` | deusyu/translate-book | MIT, Copyright (c) 2025 Rainman |
| `humanizer-zh` | ai-zixun/humanizer-zh | MIT, Copyright (c) 2026 aizixun |
| `fiction-humanizer-zh` | aa15107688191-collab/chinese-novel-writing-skills (fork by 歸藏) | MIT, Copyright (c) 2026 歸藏 |
| `translation-polish-zh` | distilled from HoraceLuBFA/en-zh-translation-polish | MIT, Copyright (c) 2026 HoraceLuBFA |

The pipeline scripts in `tools/` are original work licensed under the repository's MIT
license.

Runtime dependencies (installed separately, not vendored): BeautifulSoup4, Requests,
Pandoc (via `pypandoc-binary`), and optionally Calibre for EPUB/DOCX/PDF output.
