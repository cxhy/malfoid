# 作品进度与待办

本文件追踪具体作品的翻译、润色、审查、修复和交付状态。`AGENTS.md` 只保留通用流程、规则和文档；新增或更新作品状态写在这里。

## 作品进度

| 作品 | work_id | 章数 | 翻译 | 润色 |
|---|---|---|---|---|
| Green and Silver Book I | 95015501 | 4 | ✅ | ✅ |
| Green and Silver Book II | 95016391 | 8 | ✅ | ✅ |
| Green and Silver Book III | 95016996 | 7 | ✅ | ✅ |
| Green and Silver Book IV | 95017731 | 16 | ✅ | ✅ |
| Green and Silver Book IV（新修版） | gs-revised-book-04 | 19 | ✅ | ✅ |
| Green and Silver Book V | 95018951 | 18 | ✅ | ✅ |
| Green and Silver Book VI | 95404276 | 22 | ✅ | ✅ |
| Green and Silver Book VII | gs-book-vii | 18 | ✅ | ✅ |
| The girl who loved the boy who lived | ffn-14592941 | 15 | ✅ | ✅ |
| The Boy Who Loved the Girl Who Lived | 95603006 | 10 | 待开始 | — |
| Love at Second Sight - Book One | love-at-second-sight-book-01 | 7 | ✅ | ✅ |
| How I Met Your Mother | 75008066 | — | 待开始 | — |
| Harry Potter and the Sorcerer's Tsundere Book 1 | hp-tsundere-1667660966-book-01 | 11 | ✅ | ✅ |
| Harry Potter and the Sorcerer's Tsundere Book 2 | hp-tsundere-1667660966-book-02 | 15 | ✅ | ✅ |
| Harry Potter and the Sorcerer's Tsundere Book 3 | hp-tsundere-1667660966-book-03 | 15 | ✅ | ✅ |
| Harry Potter and the Sorcerer's Tsundere Book 4 | hp-tsundere-1667660966-book-04 | 33 | ✅ | ✅ |
| Harry Potter and the Sorcerer's Tsundere Book 5 | hp-tsundere-1667660966-book-05 | 21 | 暂停（源未完结） | — |
| Harry Potter and His First Year of Marriage | hp-first-year-of-marriage-book-01 | 5 | ✅ | ✅ |
| Worst Enemy - First Year | sb-1343487-book-01 | 1 | ✅ | ✅ |
| Worst Enemy - Second Year | sb-1343487-book-02 | 2 | ✅ | ✅ |
| Worst Enemy - Third Year | sb-1343487-book-03 | 5 | ✅ | ✅ |
| Worst Enemy - Fourth Year | sb-1343487-book-04 | 11 | ✅ | ✅ |
| The Malfoid Saga - Book One | wattpad-418446650 | 13 | ✅ | ✅ |
| The Malfoid Saga - Book Two | wattpad-418450201 | 19 | ✅ | ✅ |
| The Malfoid Saga - Book Three | wattpad-418454516 | 22 + 插曲 | 待完结 | — |

## 待办

- Love at Second Sight（作者 Veydrick，X/Twitter Articles，共 11 章、74,194 词）：第 1–7 章为 Book One（第 7 章末尾原文标注 “End of Book One.”），第 8–11 章属 Book Two（二年级，源文未完结）。Book One 已按 v2 建系列根 `works/love-at-second-sight`、接入 Ch 1–7（7 章、46,937 词）、预扫描、翻译 + 润色、全书审查、按建议修复 22 段并交付；交付标题保留英文 `Love at Second Sight - Book One`，Markdown + HTML + Part 1/2（完整 Markdown 97,986 字符，超过 80,000）。`verify_book` 0 error、0 英文残留、0 术语变体、0 覆盖缺口，8 条 canonical_missing 非阻塞 warning。审查 31 块、FAIL 25 块；修复 23 个目标中 22 个应用、1 个失败（模型输出非 JSON，原译“新时间线”本身正确，未手工改）。术语表补入 Dracaena 拼写、13 个孩子名（Alya/Cassie/Orion/Lyra/Leo/Aurora/Minerva/Delphini/Scorpius/James 等）、Mirror of Erised、Pottah 等 31 条系列术语；全局表未改。Book Two 尚未接入/翻译，等源文完结后按 Ch 8–11 续接。2026-11-27 用新的 `translation-polish-zh`（对照原文的轻改 skill）重新润色 Book One 全部 95 个正文段：按章分批、逐批落盘，失败段保留原译；2 个失败段（1 个 JSON 非法、1 个段落空行数变化）随后单独重试成功，最终 95/95 完成。章节状态备份在 `archive/repolish-20261127T040945Z/` 和 `archive/repolish-20261127T044847Z/`，旧交付文件备份在 `archive/repolish-delivery-20261127T080540Z/`。重新交付后 `verify_book` 0 error、0 英文残留、0 术语变体、0 覆盖缺口，6 条 canonical_missing 非阻塞 warning；完整 Markdown 97,748 字符，Part 1/2 字符数 56,512 / 41,236。润色结构校验补上了引号边界/配对与 `---` 分隔符检查，修复 4 个被模型改坏的段（ch3 段 16/17 引号跨段移动、ch6 段 10 多出分隔符、ch6 段 11 合并下一段）；另发现 ch6 段 11 原译漏掉了源文末尾的校医院醒来段落，已补译并重新润色。旧交付备份在 `archive/repolish-fix-20261127T082812Z/`。
- Harry Potter and the Sorcerer's Tsundere：Book 1、Book 2、Book 3、Book 4 已交付（Book 2 为 15 章，第 1–18 章；Book 3 为 15 章，第 1–22 章，1–4/11–12/18–22 为原文概述或合并段）。
- Book 5 已按 AO3 最新版（work 94081816，2026-11-17 更新）接入：21 个逻辑章（Ch 0.5–18，其中 Ch 5.5-6 为合并章），约 201,617 words；源文件 `downloads/hp-tsundere/derived/by-book/Book 5 - AO3 (Ch .5-18).md`。作者目前写到 Ch 18（DA 首次集会之后），未完结；翻译已暂停，保留 Ch 0.5 完整译文和 Ch 1 部分译文，等作者完结后再续译、预扫描、审查和交付。
- Harry Potter and His First Year of Marriage：AO3 后记性质短篇，2026-11-26 接入新增第 5 章《Mrs. Potter's Pregnancy》（1999 年 1 月），共 5 章、约 58,611 words；第 5 章已翻译、润色，全书重新交付 Markdown + HTML，作者未知。按项目规则将 Dracaena 统一译作德拉科妮娅。这本书的分卷逻辑独立：`meta.json` 设 `split_mode: per-chapter`，超过 80,000 字符时按原文一章一份拆成 Part 1–5（不再把两章合并成一份），完整 Markdown 和 HTML 仍各保留一份。verify 通过（0 error，0 英文残留，0 术语变体，0 覆盖缺口；5 条 heading_shape + 2 条 canonical_missing 非阻塞 warning）。
- Harry Potter and His First Year of Marriage 审查与修复（2026-11-26/27）：全书 67 块，170 条候选，证据核对后 109 confirmed、61 rejected、0 unresolved（按章 1:19、2:29、3:15、4:22、5:24）；确认 2 条 high、36 条 medium、71 条 low，另有 22 条风格建议单独列出。按默认规则修复 38 段（38 applied，0 failed，509 秒），跳过 71 条 low/style；修复后手工校正 6 段：恢复 3 处丢失的中文引号（1:566、2:85、2:98）、去掉 1 处 Markdown 粗体（4:333）、把 2:385 的“母亲”明确为“纳西莎”、把 2:435 的“宠坏孩子”还原为“宠孩子”。审查曾把克利切对哈利的称呼误判为“哈利少爷”，实际全书多数为“哈利主人”；已统一为“哈利主人”（50 处），避免修复引入新的称呼漂移。重新交付 5 份 Part；verify 0 error、0 英文残留、0 术语变体、0 覆盖缺口，5 条 heading_shape + 2 条 canonical_missing 非阻塞 warning。
- Book 4 审查后修复 203 段；9 段修复失败（5 处分隔符已手工校正，4 处为疑似误判/跨段书名），交付版英文残留已清零（段 25:83 已改为“一听就知”）。
- Green and Silver Book IV 新修版（2026-11-25）：AO3 原文更新为 19 章、31,674 words（旧版 16 章、23,240 words；第 4 章由 1,467 增至 1,985 words，新增第 10、11、15、17、18 章并扩写多章）。旧 v1 作品保持原样；新修版按 v2 新建系列根 `works/gs-revised-green-and-silver-revised-edition`，书 `books/book-04`。系列术语表由 Green and Silver 七本旧术语并集（222 条）初始化，合并同 id 的 Tom Ryddle/Tom Riddle 重复条目，`override_global` 保留旧系列译名（张秋、小天狼星布莱克、预言家日报、夺魂咒）；AI 对齐补入新章术语，最终 226 条。预扫描、逐章翻译与润色、全书审查、按建议修复 36 段、交付均已完成；`verify_book` 0 error、0 英文残留、0 术语变体、0 覆盖缺口，170 条 heading_shape/canonical_missing 非阻塞 warning。交付文件：`translation/Green and Silver Book IV (Revised Edition).md` + `.html`（60,255 字符，未触发分卷）。2026-11-25 将审查块上限设为 40 段后重跑全书审查：32 块、147 条候选、102 条确认、45 条驳回、14 处冲突需人工；4 条风格建议单独列出。按计划修复 43 段、0 失败；对 Digory/Cedric、截断咒语、Darcy 称呼、Snuffles 等有争议项按源文称呼统一后手工校正 7 段（决定记录：`work_temp/fix_review/decisions.md`），并把 Snuffles→斯纳夫勒斯、Greg→格雷格、Vin→文森 补入系列术语表（229 条，书副本 399 条）。重新交付 `translation/Green and Silver Book IV (Revised Edition).md` + `.html`；`verify_book` 0 error、0 英文残留、0 术语变体、0 覆盖缺口，165 条 heading_shape/canonical_missing 非阻塞 warning。
- Book 4 人名/术语对齐（2026-11-23）：统一 Alyza/Autumn/Green Streak/Snowslik/霜仙、Sirius/Pettigrew/Viktor/Fudge/Albus 等译名；修正 Hospital Wing→校医院、Witch Weekly→《女巫周刊》、Nimbus 2001→光轮2001、Skele-Gro→生骨灵，并补修“绿影/天狼星”两处；共改 166 段。系列表新增原创人物/术语并同步 5 本，交付重渲染；修改前章节备份在 `book-04/archive/name-align-20261123T041240Z/`。
- 术语严格对齐（2026-11-23）：新增 `tools/align_glossary.py`，从术语表建立 source/alias → canonical 索引，做覆盖审计、forbidden 变体扫描、canonical 组件检查和候选变体发现，全程不调模型。`verify_book` 增加 canonical_missing warning 和覆盖门禁；系列书交付默认要求覆盖审计通过（`--allow-coverage-gaps` 仅排障）。Book 4 补齐 78 个系列术语/别名、修正 Malfi→马尔福、Miss Malfoy→马尔福小姐、Dracha II 的“德拉查二号”变体；随后把 Great Hall/大厅、Care of Magical Creatures/保护神奇动物课、Nimbus 2000/光轮两千、First Task/第一项任务等 source-scoped forbidden 变体对齐，自动替换 37 处（35 段）并重交付。复核时发现 Avada Kedavra 的“索命咒/杀戮咒”和 Triwizard Tournament 的“三强杯”属于合法 canonical/描述性译法，已移除这两个 forbidden 并把 10 个被误改段落恢复到归档译文（Triwizard Cup→三强杯、Killing Curse→阿瓦达索命咒/索命咒）；另按 (index, source_hash) 恢复 ch33 idx12 的“小彼得·佩迪鲁”和 idx323 的“跳过三强争霸赛奖杯”，避免批量替换造成前缀重复和丢“奖杯”。最终覆盖审计 0 error、glossary_variant 0；verify 仍有 152 条 canonical_missing 非阻塞 warning，主要是代词和简称。审查的确定性术语确认改为只看 anchor 目标段命中 forbidden variant，并校验候选与术语相关；修复校验增加 canonical/source-scoped 检查。Book 2/3 随后完成对齐：系列表补入 Petunia、Myrtle、Aragog、Scabbers、Whomping Willow、Dark Arts、Patronus 等专名，并用 `coverage_ignore` 排除 Mum、Honestly、Wizardry 等普通词/标题碎片；同步后两本覆盖审计均为 0 error。对已确认变体执行 `--apply-forbidden`（Book 2：医疗翼→校医院、米利森特→米里森、巫师周刊→《女巫周刊》；Book 3：保护神奇动物课→保护神奇生物课、医疗翼→校医院、波皮→波比、西里斯→小天狼星、绿痕/绿影/绿色闪电→绿纹），随后按默认覆盖门禁重新交付 Book 2/3/4，未使用 `--allow-coverage-gaps`。最终 verify：Book 2/3/4 均 `ok True, errors 0, coverage_missing 0`；canonical_missing 分别为 38/33/152 条非阻塞 warning。
新增翻译前 AI 术语对齐：`glossary_align_ai.py` 用 low effort 模型把覆盖缺口判定为 alias/新词/忽略/未决，确定性校验后写入系列主表并同步书副本；系列书有未决或剩余缺口时 `translate_book_v2.py` 拒绝开始翻译。全局表不自动修改。
- 翻译/润色默认改为 `low`；预扫描和审查保持 `none`；修复用 `low`。
- Worst Enemy：Book 1–4 已翻译、润色、审查、修复并交付；book-05 五年级未接入，等作者更新完再整。审查修复：book-01 修 2 段，book-02 修 4 段并手工校正 Serpensortia/Finch-Fletchley，book-03 修 14 段并手工还原两个破折号占位段，book-04 修 17 段、1 段失败手工补修；四本 verify 全通过。
- The girl who loved the boy who lived：线上已更新到 30 章，待源站可正常下载后补第 16–30 章。
- AO3 `/works/*` 页面曾返回 Cloudflare 525；抓取脚本待恢复后验证。
- The Malfoid Saga：系列根 `works/nwbrojaneway-malfoid-saga`；Book One、Book Two 已翻译、润色、审查、修复并交付（Markdown + HTML + 分卷 Part）。Book Three 已接入源（截至 2026-11-13：22 章 + 插曲 `Ch 20.5`），按约定等完结再预扫描和翻译。
- Malfoid 源只抓 Malfoid Saga 三本，跳过作者的非马尔福作品；源站是个人站，更新时只查 `inventory.json` 的 EPUB sha256，变化才下一次 EPUB，不逐章抓页面。EPUB 解析用 `tools/epub_convert.py`；重新接入时按 `source_hash` 复用已有译文并把旧章节归档。
- Malfoid 术语：`Dracina` / `Dracina Malfoy` → 德拉科妮娅 / 德拉科妮娅·马尔福；`Caput Draconis` 译“卡普特·龙首”（避免“德拉科”子串）；`John Constantine`、`Morgoth` 已入系列主表。
- Malfoid 审查修复：Book One 40 块、176 条候选、120 条确认；修复 88 个目标中 87 个应用、1 个未变、32 个跳过。Book Two 98 块、426 条候选、278 条确认；修复 145 个目标中 143 个应用、2 个失败（1 处手工改为“你有情敌了”，另 1 处原文确有“old”，保留“老疯眼汉”）。两本 verify 通过；Book Two 仅剩插曲标题形状的非阻塞 warning。
- v2 D 阶段（完整增量更新）仍未开始；当前重新接入已能按段哈希复用译文，但跨切分预算变化时的目标迁移尚未实现。
