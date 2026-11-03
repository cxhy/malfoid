"""Pipeline tests: chunk splitting, polish checks, volume grouping, merging."""

from __future__ import annotations

import polish_chunk
import split_volumes
import translate_chunk


def test_split_text_uses_paragraph_boundary() -> None:
    text = "A" * 60 + "\n\n" + "B" * 60 + "\n\n" + "C" * 60
    parts = translate_chunk.split_text(text)
    assert len(parts) == 2
    assert "\n\n".join(parts) == text
    assert parts[0].startswith("A") and parts[1].endswith("C")


def test_split_text_single_paragraph() -> None:
    assert translate_chunk.split_text("only one paragraph") == ["only one paragraph"]


def test_clean_strips_fences_and_prefix() -> None:
    assert translate_chunk.clean("```markdown\n译文：\n你好\n```") == "你好\n"


def test_blocks_and_headings() -> None:
    text = "# 标题\n\n第一段。\n\n## 小节\n\n第二段。"
    assert polish_chunk.headings(text) == 2
    assert polish_chunk.blocks(text) == 4


def test_english_residue_ignores_urls_headings_and_mottos() -> None:
    text = (
        "# Chapter 1\n"
        "看这个链接 [link](https://example.com/page)。\n"
        "Toujours Pur 是家训。\n"
        "哈利 said hello。\n"
    )
    hits = polish_chunk.english_residue(text)
    assert len(hits) == 1
    assert "哈利" in hits[0]


def test_check_flags_missing_terms_and_forbidden_name() -> None:
    old = "# 第一章\n\n德拉科妮娅看着哈利。\n\n" + "中文" * 100
    new = "# 第一章\n\n达西看着哈利。\n\n" + "中文" * 100
    terms = [{"source": "Darcy", "target": "德拉科妮娅", "aliases": []}]
    problems = polish_chunk.check(old, new, terms, level="light")
    assert any("达西" in p for p in problems)
    assert any("missing terms" in p for p in problems)


def test_check_accepts_faithful_light_edit() -> None:
    old = "# 第一章\n\n德拉科妮娅看着哈利。\n\n" + "中文" * 100
    new = "# 第一章\n\n德拉科妮娅望了哈利一眼。\n\n" + "中文" * 100
    terms = [{"source": "Darcy", "target": "德拉科妮娅", "aliases": []}]
    assert polish_chunk.check(old, new, terms, level="light") == []


def test_check_flags_structure_change() -> None:
    old = "# 第一章\n\n第一段。\n\n第二段。\n\n第三段。"
    new = "# 第一章\n\n合并后的一段。"
    problems = polish_chunk.check(old, new, [], level="light")
    assert any(p.startswith("blocks") for p in problems)


def test_split_blocks_preserves_content() -> None:
    text = "\n\n".join(f"段落{i}" + "字" * 50 for i in range(6))
    parts = polish_chunk.split_blocks(text, 3)
    assert len(parts) == 3
    assert "\n\n".join(parts) == text


def test_chapter_year_and_interlude() -> None:
    assert split_volumes.chapter_year("# 二年级：继续") == 2
    assert split_volumes.chapter_year("## Year 4") == 4
    assert split_volumes.chapter_year("# 间章：过渡") is None
    assert split_volumes.is_interlude("# 间章：过渡")
    assert split_volumes.is_interlude("# Interlude")


def test_group_volumes_attaches_interlude_to_next_year() -> None:
    md = "# 一年级：开始\nA\n\n# 间章：过渡\nB\n\n# 二年级：继续\nC\n\n# 间章：卷末\nD\n"
    chapters = split_volumes.split_chapters(md)
    volumes = split_volumes.group_volumes(chapters)
    assert set(volumes) == {1, 2}
    assert len(volumes[1]) == 1
    assert len(volumes[2]) == 3
    assert volumes[2][0].startswith("# 间章：过渡")
    assert volumes[2][-1].startswith("# 间章：卷末")


def test_merge_natural_order() -> None:
    import merge_polished

    assert merge_polished.natural("chunk0010") == 10
    assert merge_polished.natural("chunk0002") == 2
    assert merge_polished.natural("chunk") == 0


def test_finalize_title_slug() -> None:
    import finalize

    assert finalize.title_slug("Green and Silver Book VII") == "Green_and_Silver_Book_VII"
    assert finalize.title_slug("《书名》") == "untitled"


def test_load_config_env_override(monkeypatch) -> None:
    monkeypatch.setenv("MALFOID_BASE_URL", "http://example.test/v1/")
    monkeypatch.setenv("MALFOID_API_KEY", "secret")
    assert translate_chunk.load_config() == ("http://example.test/v1", "secret")


def test_load_config_env_without_key(monkeypatch) -> None:
    monkeypatch.setenv("MALFOID_BASE_URL", "http://example.test/v1")
    monkeypatch.delenv("MALFOID_API_KEY", raising=False)
    assert translate_chunk.load_config() == ("http://example.test/v1", "")
