# 段落状态 + 等长 JSON 批协议

翻译状态从“整块 Markdown 文件”改为“每章一个段落 JSON”；翻译和润色都要求与输入段数等长的 JSON 数组，校验数量、完整性和非空。

## 背景

块级 Markdown 没有稳定的段落身份：润色允许段落数 ±10% 变化，校对按段落数各自切分，英文和中文一旦段数不同就错位；增量更新又绑定 chunk 边界，重新分块会丢复用。

## 决定

- 每章一个 `source/chapters/ch-NN.json`，每段记录 `index`、`kind`、`source`、`source_hash`、`target`。
- 段 ID = 章号 + index + source_hash；重新分块/改批不影响身份。
- 标题也是段（`kind=heading`），参与翻译。
- 翻译/润色发编号段落，要求等长 JSON 数组；校验 `len == n`、`complete`、非空。
- 协议失败重试 2 次；仍失败则逐段单独调用，绝不静默丢段。
- Markdown 由 JSON 生成，只作为兼容/交付层。

## 考虑过的选项

- **保留 chunk，只修校对对齐**：能缓解误报，但增量复用、审查定位仍然依赖块边界，治标不治本。
- **整本书一个 JSON**：写入冲突和恢复粒度太粗，弃用。

## 后果

- `translate_chunk.py`、`polish_chunk.py`、`merge_polished.py` 需要改写成段级工具；旧作品继续用旧脚本。
- 长段需要按句末切分并标记 `cont`，导出时合并。
- 现有试译 2 块要按源哈希迁移；对不上的段重译。
