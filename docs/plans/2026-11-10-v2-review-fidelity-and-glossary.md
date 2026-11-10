# v2 审查/修复：忠实度与术语问题修改计划

- 状态：**已完成**（2026-11-10；工具、规则、测试与临时书端到端验收已落地）
- 起草日期：2026-11-10
- 执行前提：**当前工作区里还有其他未完成的改动**。执行前先确认那些改动已经落地/提交，再在独立 worktree 里动手，避免覆盖别人的文件。
- 目标作品示例：`works/95017731-green-and-silver-book-iv` 第 4 章
- 相关工具：`review_book_v2.py`、`review_blocks.py`、`review_evidence.py`、`fix_review_issues.py`、`verify_book.py`、`batch_translate.py`
- 相关规则：`profiles/hp-fanfic/book_review.md`、`profiles/hp-fanfic/proofread.md`、`profiles/hp-fanfic/translation.md`

## 0. 要解决的问题

Green and Silver Book IV 第 4 章有一句：

```text
SOURCE: “Don’t you dare!, levicor….” She couldn’t finish the jinx as she suddenly felt her whole body change.
TARGET: “你敢！利维科……”她没能念完咒语，全身突然变了样。
```

同场景邻段还有一处误译：

```text
SOURCE: “Technically is a ferret” said Moody as he made Darcy fly up and down.
TARGET: “严格来说，是只白鼬。”穆迪说，一边把德拉科妮娅变来变去。
```

两处问题：

1. `levicor…` 是 **Levicorpus（官方译名“倒挂金钟”）** 的半截咒语，现译音译成“利维科”，不符合咒语官方译名规则，也不全书一致。
2. `fly up and down` 是“上下翻飞/抛上抛下”，现译“变来变去”改变了动作语义。
3. 同一句里 `felt her whole body change` 的“感觉”在润色时丢了（草稿是“突然感觉全身都变了”），属于轻微忠实度损失。

本计划要让 v2 流程能够**稳定发现并自动修复**这类问题，而不是依赖人工抽查。

## 1. 已做的实测（复现依据）

在 `/tmp/malfoid-v2-probe` 建了一个 5 段的临时 v2 书（heading + 上下文 + 这两句），实测结果：

| 配置 | 结果 |
|---|---|
| 默认审查（effort `none`） | PASS，0 issues，两处都没发现 |
| 默认审查（effort `low`） | PASS，0 issues，两处都没发现 |
| 把 TASK 行改成包含逐句忠实度、并删掉 `book_review.md` 第 1 条“不重做逐句忠实度校对” | effort `none` 抓到“变来变去”误译（severity low），仍漏“利维科” |
| 加入术语表 `Levicorpus => 倒挂金钟`（alias `levicor`，`forbidden_targets` 含“利维科”）后跑默认审查 | 审查发现术语问题，但 `review_evidence` **rejected**，理由是“evidence 里没有术语表，无法证明必须译作倒挂金钟” |
| 对同一报告跑 `fix_review_issues --dry-run` | 0 个修复目标；“变来变去”因 severity low 被跳过；术语问题因 rejected 被跳过 |
| 术语表加 `forbidden_targets` 后跑 `verify_book` | 报 `glossary_variant`，交付被拦（符合预期） |
| 手工构造一个 confirmed/medium 的术语 issue 跑 `build_fix_prompt` + 模型 | 能输出 `“你敢！倒挂金……”她没能念完咒语，全身突然变了样。` |

结论：v2 的零件大多存在，但**当前组合起来的默认路径修不了这个 case**。

## 2. 根因

按流水线顺序：

### R1. 审查规则自相矛盾，明确不做逐句忠实度

`profiles/hp-fanfic/book_review.md` 第 1 条：

> 你只审查跨章连续性，不重做逐句忠实度校对。逐句漏译、增译、误译由逐块校对负责。

`tools/review_blocks.py` 的 `build_review_prompt()` 里 TASK 行也只要求 cross-chapter consistency。但 AGENTS.md 写的是“v2 审查同时承担忠实度（漏译/增译/误译/语义反转）”。两处需要统一。

补充：v2 没有独立的逐块校对工具；v1 的 `proofread_chunk.py`/`proofread_all.py` 已归档到 `archive/v1/tools/`。所以“逐块校对负责”在 v2 里没有承接者，忠实度只能由审查承担。

### R2. 术语表没有 Levicorpus，确定性检查看不见

- `verify_book.py` 的英文残留正则 `[A-Za-z]{3,}` 对中文“利维科”无效。
- 术语表没有条目 → 没有 `forbidden_targets` → `glossary_variant` 检查不报。
- `batch_translate.relevant_glossary_terms()` 只注入命中的术语；表里没有就不会提醒翻译/润色/修复模型。
- 注意：半截咒语 `levicor…` 与全名 `Levicorpus` 不构成整词匹配。若要让注入命中，术语条目的 `aliases` 必须包含截断形式 `levicor`。

### R3. 证据核对不注入术语表，术语候选会自我否决

`tools/review_evidence.py` 的 `build_evidence_prompt()` 只给候选 message/proposal 和有界上下文段落，不带术语表或正典参考。`verify_candidates()` 也没有术语表参数。结果：依赖术语表的候选会被判“unsupported”。

### R4. 修复器只修 high/medium，确认了也可能不修

`tools/fix_review_issues.py` 的 `AUTOFIX_SEVERITIES = {"high", "medium"}`。模型给误译标 low 时，`collect_targets()` 直接跳过。术语问题若被证据层 rejected，也不会进入修复。

### R5. Book IV 是 v1 状态，v2 没有入口

`works/95017731-green-and-silver-book-iv/source/chapters/` 只有 `ch-NN.md`，没有 `ch-NN.json`。`review_book_v2.load_chapters()` 返回空列表，`review_book()` 报 “book has no chapter state”。所以对这本书，v2 必须先迁移，或手工修；不能直接跑审查/修复。

## 3. 修改步骤

### Step 0：隔离与基线

1. 确认当前其他工作已落地；`git status` 里不要带着别人的未提交改动开工。
2. 建独立 worktree/branch，例如 `git worktree add ../malfoid-v2-fidelity -b fix/v2-review-fidelity`。
3. 先跑现有测试，记录基线：
   ```bash
   .venv/bin/python -m pytest tests/test_review_blocks.py tests/test_review_evidence.py \
     tests/test_review_book_v2.py tests/test_fix_review_issues.py tests/test_verify_book.py
   ```
4. 不要改 `translation/` 下的任何已交付正文；本计划改工具、规则和测试。

### Step 1：让审查职责覆盖逐句忠实度（R1）

文件：
- `profiles/hp-fanfic/book_review.md`
- `tools/review_blocks.py`（`build_review_prompt()` 的 TASK 行）

改法：
- 把 `book_review.md` 第 1 条改成类似：
  > 你同时审查逐句忠实度和跨章连续性：漏译、增译、误译、语义反转、术语/咒语错误，以及跨章漂移和前后矛盾都要报。
- 把 TASK 行改成明确包含 fidelity：
  > Review these numbered source/target segment pairs for sentence-level fidelity and cross-chapter consistency: omission, addition, mistranslation, semantic reversal, term and spell accuracy, name drift, pronoun and gender use, timeline and character state, premature spoilers, AU rule breaks and tone drift.

验收：
- `tests/test_review_blocks.py` 增加断言：prompt 包含 `omission`/`mistranslation`/`semantic reversal` 等词；不再出现“只审查跨章连续性”或“不重做逐句忠实度校对”。
- 用临时书跑 `review_book_v2.py --effort none --limit-blocks 1`，应能报出“变来变去”误译。
- 注意：不要只改 TASK 行而保留 profile 里的否定句；两处都要改，否则模型仍会被规则带偏。

### Step 2：证据核对能看到术语表（R3）

文件：
- `tools/review_evidence.py`（`verify_candidates()`、`verify_candidate()`、`build_evidence_prompt()`）
- `tools/review_book_v2.py`（`verify_and_arbitrate()` 调用处）

改法（二选一，推荐两者都做）：
1. 把 `glossary_terms`/格式化后的 glossary 文本传进 `verify_candidates()`，并在 `build_evidence_prompt()` 里加一段 `GLOSSARY (canonical targets)`。这样验证模型能判断术语候选。
2. **更稳的确定性路径**：在 `verify_candidates()` 里，对 `category == "terminology"` 且候选文本/目标段命中术语表 `forbidden_targets` 的候选，直接标记 `confirmed`，不调用模型。术语表是项目权威，不需要模型再裁决。注意只对精确命中做确定性确认，避免把普通术语建议也自动确认。

验收：
- 单测：构造一个 terminology 候选 + 术语表，断言状态是 `confirmed`；没有术语表时仍走模型/保持 rejected。
- 端到端：临时书加术语表后跑审查，术语候选必须是 `confirmed`，不能被 rejected。

### Step 3：修复器处理术语违规（R4）

文件：
- `tools/fix_review_issues.py`（`collect_targets()`、`classify_issue()`）

改法（推荐最小改动）：
- 对 confirmed issue，如果目标段当前译文里出现术语表 `forbidden_targets` 的变体，视为可修复，不受 `severity` 限制（或把严重度下限抬到 medium）。
- 仍然只修 `terminology`/`mistranslation`/`omission`/`addition`/`semantic_reversal`/`consistency`；style 继续跳过。
- 不要全局放开 low severity（会引入噪声），只对“确定性术语违规”放开。

验收：
- 单测：low severity 的 glossary variant 也能被 `collect_targets()` 收进 targets；普通 low severity 误译仍按现有策略跳过（除非你决定一并放开）。
- 端到端：`fix_review_issues --dry-run` 应列出第 4 章那句咒语段；实际跑修复后得到 `“你敢！倒挂金……”`。

### Step 4：补术语数据（R2）

对 v2 系列：
- 在对应系列 `glossary.json` 增加条目：
  ```json
  {
    "id": "Levicorpus",
    "source": "Levicorpus",
    "target": "倒挂金钟",
    "aliases": ["levicor"],
    "category": "spell",
    "confidence": "high",
    "notes": "官方译名；半截咒语保留截断形式，如“倒挂金…”",
    "forbidden_targets": ["利维科", "利维科斯"]
  }
  ```
- `aliases` 必须含 `levicor`，否则 `levicor…` 不会命中术语注入。
- 同步到书级副本：`sync_glossary.py` 或 `book_state.load_context_terms()` 会自动同步。

对 v1 作品（Book IV）：
- v1 没有 `glossary.json` 的机器可读路径（只有 `glossary.md`），`verify_book`/v2 审查不会跑。
- 两个选项：
  1. **手工修**：改 `translation/polished/book.md` 和 `translation/Green_and_Silver_Book_IV.md`，再重新生成 HTML。这是当前最快路径。
  2. **迁移后跑 v2**：用 `preview_pipeline.py` 把 legacy chunks 迁成段落状态，再审查/修复。迁移会按 source hash 复用 polished 译文，未匹配段才重译；成本较高，需单独评估。
- 本计划不自动改 v1 已交付正文；迁移或手修由执行者明确选择并记录。

验收：
- `verify_book.py` 在含“利维科”的 v2 书上报 `glossary_variant`。
- `batch_translate.relevant_glossary_terms()` 对 `levicor…` 能选中 Levicorpus 条目。

### Step 5：收尾与文档

- 更新 `docs/pipeline.md` 的审查段：明确“v2 审查承担逐句忠实度 + 跨章连续性”，并说明术语候选的证据核对会读取术语表。
- 更新 `docs/tools.md` 中对 `review_evidence` / `fix_review_issues` 的描述（术语表注入、确定性术语确认、修复严重度策略）。
- 如改动影响 AGENTS.md 的“关键规则/失败处理”，同步更新（但注意当前工作区可能有人在改 AGENTS.md，先检查 diff）。

## 4. 端到端验收（必须跑）

用一个临时 v2 书（不要动正式作品），至少包含：

- 段 1：上下文
- 段 2：`levicor…` / “利维科”
- 段 3：`fly up and down` / “变来变去”
- 术语表含 Levicorpus 条目（alias `levicor`，forbidden target `利维科`）

验收流程：

1. 审查：
   ```bash
   .venv/bin/python tools/review_book_v2.py <tmp-book> --effort none --no-preflight --limit-blocks 1
   ```
   期望：至少 2 个 issue；术语 issue `confirmed`；误译 issue 可修复。
2. 修复计划：
   ```bash
   .venv/bin/python tools/fix_review_issues.py <tmp-book> --dry-run
   ```
   期望：列出咒语段和误译段，不是 0 targets。
3. 修复：
   ```bash
   .venv/bin/python tools/fix_review_issues.py <tmp-book> --effort low
   ```
   期望：咒语段变成 `“你敢！倒挂金……”`（保留截断）；误译段变成“上下翻飞/抛上抛下”一类，不再是“变来变去”。
4. 交付校验：
   ```bash
   .venv/bin/python tools/verify_book.py <tmp-book>
   ```
   期望：`ok: true`。
5. 全量测试：
   ```bash
   .venv/bin/python -m pytest tests/test_review_blocks.py tests/test_review_evidence.py \
     tests/test_review_book_v2.py tests/test_fix_review_issues.py tests/test_verify_book.py
   ```
   期望：全绿，且没有为了通过而改旧测试语义。

## 5. 约束与坑

- **不要重译**：修复只改单段 target，不合并/拆分段落，不加译注。
- **保留截断形式**：半截咒语应输出“倒挂金…”，不是完整“倒挂金钟……”。修复 prompt 里要保留原文 `…` 的截断语义。
- **alias 匹配**：`levicor…` 要命中 `Levicorpus`，必须靠 alias `levicor`；不要把 alias 写成 `Levicorpus` 就算完。
- **不要全局放开 low severity**：只对术语表 `forbidden_targets` 命中的确定性违规放开，否则审查噪声会进入自动修复。
- **证据层确定性确认要限定范围**：只对 terminology + 精确 glossary variant 生效；不要把所有术语建议都自动 confirmed。
- **v1 不要假装能跑 v2**：Book IV 没有 `ch-NN.json` 时，明确报错/记录“需要迁移或手修”，不要静默跳过。
- **并发保护**：当前工作区已有多个文件在改（`tools/review_*.py`、`tools/fix_review_issues.py`、`profiles/...`、`tests/...`）。开工前先 `git diff` 看最新状态，必要时等那些改动提交后再改，避免把别人的修改覆盖掉。

## 6. 待决问题

1. 误译的 low severity 是否一律允许自动修复？建议先只放开“确定性术语违规”，误译 low 仍留给人工，观察一轮误报率再决定。
2. 证据层是否完全跳过模型做术语确认？建议 terminology 精确命中 glossary 时跳过模型；其他仍走模型，避免把“术语建议”变成“术语强制”。
3. Book IV 是手工修还是迁移到 v2？建议本轮先手工修这两句并记录，迁移单独排期，避免把交付节奏拖长。

## 7. 关键代码位置

- `tools/review_blocks.py`：`build_review_prompt()` 的 TASK 行、`_protocol_contract()`
- `tools/review_book_v2.py`：`review_book()` 的 rules 组装、`verify_and_arbitrate()`
- `tools/review_evidence.py`：`build_evidence_prompt()`、`verify_candidate()`、`verify_candidates()`
- `tools/fix_review_issues.py`：`AUTOFIX_SEVERITIES`、`collect_targets()`、`classify_issue()`、`build_fix_prompt()`
- `tools/verify_book.py`：`_glossary_variants()`、`verify_book()`
- `tools/batch_translate.py`：`relevant_glossary_terms()`、`_needle_occurs()`
- `profiles/hp-fanfic/book_review.md`：第 1 条
- `profiles/hp-fanfic/proofread.md`：第 5、6 条（术语/咒语）
- `profiles/hp-fanfic/translation.md`：第 1、8 条（术语表/咒语）

## 8. 交付判定

满足以下全部条件才算完成：

- [x] 审查 prompt 明确包含逐句忠实度，且不再有“只审查跨章连续性”的否定规则。
- [x] 术语候选在证据层能读取术语表；glossary variant 精确命中时确定性 confirmed。
- [x] low severity 的确定性术语违规能被修复器收集并修复。
- [x] 临时书端到端：审查 → 确认 → 修复 → `verify_book` 全通过。
- [x] 正式 v2 书的翻译/润色/修复 prompt 都能注入 Levicorpus 条目（alias `levicor` 命中）。
- [x] 没有改动 v1 已交付正文（除非执行者明确选择手修并记录）。
- [x] 相关单测和全量测试通过，未新增只复述实现的空测试。

## 附录 A：临时复现书的最小结构

目录：

```text
/tmp/malfoid-v2-probe/
├── series.json
├── glossary.json
└── books/book-01/
    ├── meta.json
    └── source/chapters/ch-01.json
```

`series.json`：

```json
{"series_id":"probe","title":"Probe","author":"tester","status":"in-progress",
 "books":[{"number":1,"slug":"book-01","title":"Book 1","status":"in-progress"}]}
```

`glossary.json`（先留空 terms，复现“默认漏报”；再按 Step 4 加 Levicorpus 条目复现“证据否决”）：

```json
{"version":2,"high_frequency_top_n":20,"applied_meta_hashes":{},"terms":[]}
```

`meta.json`：

```json
{"work_id":"probe-book-01","title":"Probe Book 1","author":"tester",
 "profile":"hp-fanfic","series_id":"probe","book_number":1}
```

`source/chapters/ch-01.json` 的 segments（source_hash 用 `segment_state.source_hash()` 生成）：

```json
[
  {"index":0,"kind":"heading","source":"Ch 1","target":"第1章","level":1},
  {"index":1,"kind":"text",
   "source":"“Darcy stop!” Potter took out his wand as well, they were facing each other just as two years ago in that stupid Duel Club.",
   "target":"“德拉科妮娅，住手！”波特也抽出了魔杖。两人面对面站着，就像两年前在那个愚蠢的决斗俱乐部里一样。"},
  {"index":2,"kind":"text",
   "source":"“Don’t you dare!, levicor….” She couldn’t finish the jinx as she suddenly felt her whole body change.",
   "target":"“你敢！利维科……”她没能念完咒语，全身突然变了样。"},
  {"index":3,"kind":"text",
   "source":"Harry saw how Darcy got turned into a white ferret, and from behind a nearby tree professor Moody emerged. “Attacking someone from behind Miss Malfoy?”",
   "target":"哈利看见德拉科妮娅变成了一只白鼬，穆迪教授从附近一棵树后走了出来。“从背后袭击别人，马尔福小姐？”"},
  {"index":4,"kind":"text",
   "source":"“Technically is a ferret” said Moody as he made Darcy fly up and down.",
   "target":"“严格来说，是只白鼬。”穆迪说，一边把德拉科妮娅变来变去。"}
]
```

每个 segment 必须带 `source_hash`，否则审查/修复的幂等键不完整。用一段 Python 生成即可：

```python
import json, sys
from pathlib import Path
sys.path.insert(0, "tools")
import segment_state
segments = [...]  # 上面的列表
for segment in segments:
    segment["source_hash"] = segment_state.source_hash(segment["source"])
Path("source/chapters/ch-01.json").write_text(
    json.dumps({"chapter": 1, "title": "Ch 1", "heading_level": 1, "segments": segments},
               ensure_ascii=False, indent=2)
)
```

复现命令：

```bash
# 默认漏报
.venv/bin/python tools/review_book_v2.py /tmp/malfoid-v2-probe/books/book-01 \
  --effort none --no-preflight --limit-blocks 1 --no-cache

# 改 prompt 后能抓到误译
# （按 Step 1 改 book_review.md 与 review_blocks.py 后重跑）

# 加术语表后：审查发现术语问题，但证据层 rejected
# （按 Step 4 加 Levicorpus 条目后重跑，观察 evidence[].status）

# 修复器：0 targets / low severity skipped
.venv/bin/python tools/fix_review_issues.py /tmp/malfoid-v2-probe/books/book-01 --dry-run
```
