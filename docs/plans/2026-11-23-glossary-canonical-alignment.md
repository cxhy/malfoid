# 术语表严格对齐：修改计划

- 状态：**Phase 1 已完成，Phase 2 部分完成**（2026-11-23）
- 目标：把“跨段专名/术语一致性”从模型生成任务改成确定性的查表与校验任务。
- 适用流程：翻译、润色、审查、修复、交付、`verify_book`
- 相关工具：`book_state.py`、`batch_translate.py`、`polish_batch.py`、`review_evidence.py`、`fix_review_issues.py`、`verify_book.py`、`book_delivery.py`
- 相关数据：`glossary/hp.json`、系列 `glossary.json`、书级 `work_temp/glossary.json`

## 0. 要解决的问题

Book 4 修复前存在大量跨段漂移：

| 名字 | 修复前写法 |
|---|---|
| Alyza | 阿莉莎 127 / 阿莉扎 24 / 艾莉莎 25 |
| Autumn | 奥特姆 15 / 奥顿 19 / 奥滕 12 |
| Sirius | 小天狼星 213 / 西里斯 21 |
| Pettigrew | 小矮星彼得 3 / 彼得·佩迪鲁 7 |
| Hospital Wing | 校医院 23 / 医疗翼 15 |

同一名字在不同段落被译成不同写法，交付前没有被稳定拦住。

## 1. 根因

按流水线顺序：

### R1. 术语表缺覆盖，原创名和变体拼写没有 canonical

翻译按 token 预算分批，每批只注入本批出现的术语。术语表没有的原创名，模型按当批上下文自由音译；不同批得到不同写法。

### R2. 术语注入只是提示，没有生成后校验

`batch_translate.relevant_glossary_terms()` 会把命中的术语写进 prompt，但翻译和润色之后没有检查 canonical target 是否真的出现。模型可以忽略术语表。

### R3. 审查确认“不一致”，但不裁决“用哪个”

`review_evidence` 能确认某个名字前后不一致，但 canonical 仍由候选的 `proposal` 决定。Book 4 中同一 Alyza 的三个候选都被 confirmed，proposal 分别是“阿莉莎”“艾莉莎”“阿莉扎”，修复器逐段执行，漂移没有收敛。

### R4. 修复和交付没有全局 canonical 检查

- `fix_review_issues.py` 逐段调模型，没有全书 name table。
- `validate_fix()` 只查“达西/德拉科”和英文残留。
- `verify_book.py` 只查 `forbidden_targets`；没有 forbidden 的名字漂移不可见。

### R5. 审查报告缺 category/severity，确定性术语路径失效

`validate_review_output()` 不强制 `category`/`severity`。当前报告里的 issue 大多没有这两个字段；`review_evidence._deterministic_terminology_result()` 要求 `category == "terminology"`，所以 `forbidden_targets` 命中也没有走确定性确认。

## 2. 设计原则

1. **术语表是权威**：专名、地名、咒语、物品的 canonical target 只从术语表读取，不由模型临场决定。
2. **确定性优先**：名字对齐、变体扫描、覆盖审计都不调模型。
3. **覆盖先于翻译**：原文专名必须在术语表中有条目或 alias，否则不允许交付。
4. **变体先于自动修复**：先发现候选变体，人工确认写入 `forbidden_targets`；自动修复只处理已确认的 forbidden variant。
5. **缩写合法**：`阿莉莎·特威格斯` 允许正文使用 `阿莉莎` 或 `特威格斯`；`哈利·波特` 允许 `哈利` 或 `波特`。
6. **代词合法**：原文有名字、译文用代词是正常翻译，不能一律判错；`canonical_missing` 先作为 warning/报告，不直接阻断。

## 3. 修改步骤

### Phase 1：确定性对齐工具与交付门禁

#### Step 1.1 新增 `tools/align_glossary.py`

职责：

- 读取 `book_state.load_glossary_terms(book)`，建立 canonical map。
- `source` + `aliases` 作为原文匹配形式；`target` 拆出完整形式和组件形式。
- 扫描每个 segment：
  - 原文命中术语，译文出现 `forbidden_targets` → `glossary_variant` error。
  - 原文命中术语，译文出现 canonical target 或组件 → OK。
  - 原文命中术语，译文既没有 canonical 也没有 forbidden → `canonical_missing` warning。
- 覆盖审计：从原文抽取专名，排除 heading、常见英文词、术语表已覆盖形式、profile 的 female Draco source forms；输出未覆盖专名。
- 变体发现：对未命中 canonical 的段落，抽取高频中文候选串，过滤常见词，输出候选变体供人工确认。
- 可选 `--apply-forbidden`：只替换已确认的 forbidden variant，按 canonical 组件长度选择替换形式；默认 dry-run。

#### Step 1.2 接入 `verify_book.py`

新增参数：

- `strict_coverage: bool = False`
- `alignment: bool = True`

行为：

- `glossary_variant` 继续是 error。
- `canonical_missing` 是 warning。
- `strict_coverage=True` 时，未覆盖专名（出现次数 >= 2）是 error。
- 报告新增 `alignment` 段，列出 variant/missing/coverage 计数。

#### Step 1.3 接入 `book_delivery.py`

- 系列书默认 `strict_coverage=True`；legacy flat work 默认不强制。
- 交付前先跑覆盖审计和对齐扫描；有 error 拒绝交付。
- 不自动改正文；自动替换只通过 `align_glossary.py --apply-forbidden` 显式执行。

#### Step 1.4 测试

- `tests/test_align_glossary.py`：
  - canonical 组件缩写通过。
  - forbidden variant 报 error。
  - 缺覆盖专名在 strict 下报 error。
  - 变体发现能找出高频候选。
  - `--apply-forbidden` 只替换已确认变体。
- `tests/test_verify_book.py`：
  - `canonical_missing` 是 warning。
  - `strict_coverage` 缺名是 error。
- `tests/test_book_delivery.py`：
  - 系列书缺覆盖时拒绝交付。
  - legacy flat work 不被覆盖门禁误伤。

### Phase 2：审查/修复收敛到 canonical

#### Step 2.1 审查协议强制 category/severity（暂缓）

- 当前实现先保证旧报告兼容：`review_evidence` 的确定性术语确认不再要求 `category`。
- 新审查 prompt 已要求 `category`/`severity`；校验层强制留到后续，避免打断现有报告和测试。

#### Step 2.2 术语候选走确定性确认（已完成）

- `review_evidence._deterministic_terminology_result()` 不再依赖 `category`；候选 anchor 目标段精确命中 glossary `forbidden_targets` 时确定性 confirmed。
- 只认目标段命中，避免候选 message 里引用变体也误确认。

#### Step 2.3 修复器按 canonical 收敛（部分完成）

- `validate_fix()` 已增加 glossary 检查：修复结果不得包含任何 forbidden variant；当前译文带 confirmed variant 时，修复结果必须包含 canonical 或其组件。
- 修复结束后跑一次 `align_glossary.scan_alignment()`，结果写入 `post_alignment_errors`。
- 按 term 全局分组和“没有 glossary 时用首次/最高频”留到后续。

### Phase 3：数据与文档

- 给当前 Book 4 补齐术语表和 `forbidden_targets`，跑 `align_glossary.py --discover` 复核。
- 更新 `docs/pipeline.md`、`docs/tools.md`、`AGENTS.md` 的关键规则：术语覆盖门禁、确定性对齐、交付校验。
- 更新 `docs/progress.md` 记录本次修复。

## 4. 验收

- [x] `align_glossary.py` 能对 Book 4 输出完整的 variant/missing/coverage 报告。
- [x] Book 4 在 `strict_coverage=True --min-count 3` 下覆盖审计无 error。
- [x] Book 4 在 `verify_book(strict_coverage=True)` 下 `glossary_variant` 为 0。
- [x] 系列书交付默认启用覆盖门禁；legacy flat work 不强制。
- [x] 现有测试与新增测试全绿。
- [x] 新增测试覆盖 canonical 组件、forbidden variant、覆盖门禁、变体发现、修复校验。
- [x] 不改已交付正文以外的自动替换；`--apply-forbidden` 是显式命令，本次共替换 Book 4 37 处（35 段）source-scoped 变体；Avada Kedavra/Triwizard Tournament 的 10 处误改已恢复。

## 5. 约束

- **不重译**：对齐只改 target 中的已确认变体，不合并/拆分段落。
- **不自动裁决新名字**：变体发现只输出候选，不直接写入 `forbidden_targets`。
- **通用词变体必须 source-scoped**：如 Great Hall 的“大厅”，设 `forbidden_source_scoped: true`，只在 source 命中术语的段落检查/替换，避免误伤无关 prose。
- **被 canonical 包含的 forbidden 变体合法**：`德拉科` 不判 `德拉科妮娅`，`索命咒` 不判 `阿瓦达索命咒`；只在 target 实际包含拥有该子串的 canonical 时跳过。
- **共享 source/alias 不丢术语**：索引按术语保留全部条目，避免 `Killing Curse` 因 `Avada Kedavra` 占用 alias 而被整条丢弃。
- **不把代词判错**：`canonical_missing` 默认 warning；strict canonical 只作为可选诊断。
- **术语表同步语义不变**：本阶段不改 `sync_glossary` 的合并规则，避免影响现有系列。
- **旧报告兼容**：Phase 2 的 category/severity 强制只对新审查生效；旧报告读取时补全。
- **保护未提交改动**：当前工作区有其他改动，修改前先看 `git diff`，不要覆盖无关文件。

## 6. 待决问题

1. 覆盖审计的英文专名抽取用启发式，可能漏掉小写化名或全大写拼写；是否需要在 prescan 阶段生成候选名清单并人工确认？
2. 自动替换 `forbidden_targets` 时，组件长度启发式是否足够？遇到无法安全判断的形式，应只报告不替换。
3. `canonical_missing` 是否要在某些类别（person/place）上升级为 error？先观察一轮误报率再决定。

## 7. 关键代码位置

- `tools/book_state.py`：`load_glossary_terms()`、`merged_glossary_terms()`
- `tools/batch_translate.py`：`relevant_glossary_terms()`、`_needle_occurs()`
- `tools/polish_batch.py`：`_glossary_lines()`
- `tools/review_evidence.py`：`_deterministic_terminology_result()`
- `tools/fix_review_issues.py`：`collect_targets()`、`validate_fix()`
- `tools/verify_book.py`：`_glossary_variants()`、`verify_book()`
- `tools/book_delivery.py`：`write_delivery()`
- `profiles/hp-fanfic/manifest.json`：`female_draco.source_forms`
