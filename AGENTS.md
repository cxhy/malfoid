# malfoid — AO3 性转马尔福同人翻译工作区

源文本是 AO3 / FicHub / PDF / Wattpad 上的 Draco Malfoy 性转同人，目标语言中文，交付 Markdown + HTML。所有状态都在工作目录里，仓库只放工具、规则和文档。

**当前只使用 v2 流程。** v1 工具已归档到 `archive/v1/tools/`，不再作为日常入口；v1 作品保持原样，不迁移。

## 目录约定

```text
glossary/hp.json                   # 全局术语表（原著正典 + 马尔福统一规则；tracked）
works/<series-id>-<slug>/          # 一个系列一个根目录
├── series.json                    # 系列标题、作者、书列表、顺序、状态
├── glossary.json                  # 系列主表（全局基线 + 本系列补充/覆盖）
├── story_bible.md                 # 系列级设定（每本书完成后合并）
└── books/book-NN/                 # 一本书一个子 work，翻译任务的执行粒度
    ├── meta.json
    ├── source/chapters/ch-NN.json # 段落状态（index/source/source_hash/target）
    ├── work_temp/                 # 术语副本、预扫描缓存
    └── translation/
        ├── <Title>.md / .html     # 完整 Markdown + 单份 HTML
        └── <Title> - Part N.md    # 按章均匀切分（超过 80,000 字符时）
        └── review/                # 结构化审查报告 + 汇总
```

内容目录（`works/`、源文件）git-ignored；全局术语表 `glossary/hp.json` 入库，修改需单独审阅。

## 翻译流程（v2）

0. **接入原文**：AO3 / FicHub / PDF / Wattpad TXT 用转换器生成 `source/work.md` + `meta.json`；TXT/Markdown 也可以直接接入 v2。
1. **建系列/书 + 接入**：`ingest_book_v2.py` 创建系列根、书子 work，把多个源文件接成连续章号，写 `source/chapters/ch-NN.json`，并设置 `source_complete`；范围标记（如 `Ch 1-4`、`Ch18-19-20`）会记录 `covered_chapters`。
2. **术语表**：三层——全局表（`glossary/hp.json`，只收原著 HP 正典译名和性转马尔福统一规则）、系列主表（本系列所有书共用，可补系列特有词，覆盖正典须标 `override_global`）、书副本（全局 + 系列 + 本书本地补充）。书内新词先写副本，交付前用 `sync_glossary.py --merge-back` 回并系列主表，不回并全局表；翻译时只注入当前批实际出现的术语。
3. **预扫描**：整本书源齐了再做；`prescan_book.py` 按章并行（默认 2），生成 digest、style brief、synopsis、book story bible，并合并进系列 bible。
4. **翻译**：`translate_book_v2.py` 按 token 预算打包，等长 JSON 数组，校验数量/完整/非空；协议失败重试 2 次，最后逐段回退，不静默丢段。
5. **润色**：翻译入口默认接着润色，固定轻改；只改句子、节奏、对白和翻译腔，段落顺序不变，不新增原文没有的动作、感官细节或心理活动。
6. **审查**：`review_book_v2.py` 按全书连续块审查；结构化 JSON + JSON mode，非法输出递归二分到单段，单段有限重试，总调用有预算；原始失败响应落 `work_temp/review_traces/`；候选经证据核对，矛盾做冲突仲裁；只出建议，不改正文。审查注入 profile 规则，避免把 AU 改动“纠正”回原著。v2 审查同时承担忠实度（漏译/增译/误译/语义反转）和跨章一致性；证据核对会读取术语表，`terminology` 候选精确命中 `forbidden_targets` 时确定性确认，不再被证据层自我否决。
7. **交付**：`book_delivery.py` 先自动把书内新词回并系列主表并同步全系列（不回并全局表；有冲突默认拒绝交付，`--allow-glossary-conflicts` 仅排障），再从段落状态渲染完整 `translation/<Title>.md`，用 `build_html.py` + `template.html` 生成单份 HTML；Markdown 超过 80,000 字符时，额外按章边界均匀切出 `translation/<Title> - Part N.md`。

## 关键规则

- **性转马尔福**：全名德拉科妮娅·马尔福，单独出现德拉科妮娅，昵称德拉；代词“她”；禁止“达西”和“德拉科”。
- **术语先行**：人物、地名、咒语、学院、专有物品按官方译名固定；全局表只和原著 HP 对齐（性转马尔福统一为德拉科妮娅是唯一项目例外），系列特有词进系列主表，书内新词回并系列主表。表里没有的专名按通行译法音译并全书一致。术语表支持 `forbidden_targets`，交付前的漂移扫描会拦截旧译名。
- **译文标准**：读起来像中文原创小说；中文标点、中文引号；不保留英文原词；不添加译者注。
- **模型**：只用本机 pi 的本地模型 `deepseek-v4-1-flash/DeepSeek-V4.1-Flash-1017-TensorFold-Zig`；推理 token 计入 `max_tokens`；翻译/润色默认 low（质量优先，慢一点可以接受）；预扫描和审查默认 none；审查后的修复默认 low。本地端点默认单流；启动并行任务前先跑 `tools/preflight_model.py`。
- **原文更新**：旧版本 `source/`、`work_temp/`、`meta.json`、译文和交付文件归档到 `<book>/archive/<时间戳>/`，不删除。

## 失败处理（v2）

- 翻译/润色：等长/完整/非空校验失败先重试 2 次；仍失败逐段回退；每段最终有 target 或记录 failure。
- 审查：结构化输出不合法才递归二分到单段；单段重试有上限，单块总调用有预算；失败响应落 trace，已完成的块按 content + rules 指纹缓存；术语候选命中 glossary variant 时确定性 confirmed，不消耗模型调用。
- 预扫描：digest 溢出按段落递归切半再合并；style/synopsis/bible 按 digest 上下文哈希缓存，失败不写半成品。
- 模型溢出：推理 token 计入 completion；空输出或截断时先切小，再降 effort。

## 操作命令（v2）

```bash
# 0. 原文 -> Markdown（可选；TXT/Markdown 可直接接入 v2）
.venv/bin/python tools/ao3_convert.py <下载的.html>
.venv/bin/python tools/fic_convert.py <FicHub下载的.html>
.venv/bin/python tools/pdf_convert.py <下载的.pdf> --meta-from <AO3.html> --work-id <id>
.venv/bin/python tools/txt_convert.py "<source-dir>"

# 1. 建系列/书 + 接入多个源文件（连续章号，标记完成）
.venv/bin/python tools/ingest_book_v2.py \
  --series-root works/<series-id>-<slug> \
  --series-id <series-id> \
  --title "<series-title>" \
  --author "<author>" \
  --book 1 --title-book "Book 1" \
  --source "<source-file-1>" "<source-file-2>"

# 2. 预扫描（整本书源齐了再跑；默认 effort none）
.venv/bin/python tools/prescan_book.py works/<series>/books/book-01 --workers 1

# 3. 翻译 + 润色（默认润色、effort low；按批落盘，--max-seconds 到时优雅退出）
.venv/bin/python tools/translate_book_v2.py works/<series>/books/book-01 --effort low
.venv/bin/python tools/translate_book_v2.py works/<series>/books/book-01 --max-seconds 3000

# 4. 全书审查（issues 带 category/severity；风格建议单独列；--workers 省略时自动跑 preflight；改 prompt/effort/profile 后先 --limit-blocks 1 小样验证（写到 review/smoke））
.venv/bin/python tools/review_book_v2.py works/<series>/books/book-01 --effort none
.venv/bin/python tools/review_book_v2.py works/<series>/books/book-01 --effort none --limit-blocks 1

# 5. 按审查建议修复（只自动修复误译/漏译/增译/语义反转/一致性问题；风格建议跳过；确定性术语变体命中 forbidden_targets 时不受 low severity 限制；审查后手工改过的段会按审查时译文识别并跳过；--dry-run 只出计划、不调模型；默认幂等续跑、effort low）
.venv/bin/python tools/fix_review_issues.py works/<series>/books/book-01 --effort low

# 6. 交付 Markdown + HTML（先自动回并术语到系列主表并同步全系列；默认先跑 verify_book，失败不交付；章标题按章号/范围确定性生成）
.venv/bin/python tools/book_delivery.py works/<series>/books/book-01 \
  --title "<book-title>" --author "<author>"

# 6b. 术语表同步（全局 + 系列 -> 书级副本；交付会自动回并，手动 --merge-back 用于交付前单独整理）
.venv/bin/python tools/sync_glossary.py works/<series>
.venv/bin/python tools/sync_glossary.py works/<series> --merge-back

# 7. 导出人读版术语表
.venv/bin/python tools/export_glossary.py works/<series>/books/book-01

# 8. 试译迁移（v1 旧块 -> v2 段落状态）
.venv/bin/python tools/preview_pipeline.py works/<legacy-work> --chunks 1 2
.venv/bin/python tools/review_preview.py works/<legacy-work>
```

Pandoc 在项目 venv 里；`build_html.py` 需要 PATH 中包含 pypandoc 自带的 pandoc。

## 交付规则

- 一本书一个 work；单学年/单卷作品交付完整 `translation/<作品原名>.md` + 单份 `.html`；超过 80,000 字符时再交付按章均匀切分的 `Part N.md`。
- 分卷只按章边界切；单章本身超过上限直接报错，不切章内。完整 Markdown 保留为母版，不计入分卷上限。
- 跨学年作品按书拆成多个 work，每本书单独交付，不再用 v1 的分卷脚本。
- 最终 HTML 必须走 `tools/build_html.py` 套 `template.html`，不能裸 Pandoc。
- 交付前自动跑 `tools/verify_book.py`：完整性、禁用称谓、英文残留、术语变体、章标题；有 error 就拒绝交付，`--skip-verify` 仅供排障。
- `translation/` 顶层只放最终文件；中间产物放 `translation/review/`、`work_temp/prescan/` 等。

## 开发进度

| 阶段 | 内容 | 状态 |
|---|---|---|
| A | 系列根 + 书子 work；段落状态 + 多源接入 + Markdown 重建 | ✅ |
| B | 翻译/润色等长 JSON 批协议；试译迁移 | ✅ |
| C | 审查：全书块、并行、结构化校验、证据核对、冲突仲裁、报告 | ✅ |
| D | 增量更新：段落级持久化 + 源哈希，chunk-size 不再影响复用 | 设计已定，未开始 |

具体作品的翻译、润色、审查、修复和交付状态见 `docs/progress.md`。

## 参考

- `docs/pipeline.md`：流水线契约和目录布局。
- `docs/tools.md`：工具输入输出。
- `docs/progress.md`：具体作品进度、待办和审查修复记录。
- `profiles/hp-fanfic/`：HP 同人专用规则。
- `GLOSSARY.md`：项目术语。
- `docs/adr/`：架构决策。
- `archive/v1/`：已归档的 v1 工具和测试，不再使用。
