"""Profile rule composition tests, including skill-driven polish."""

from __future__ import annotations

import profile_rules


def test_skill_text_strips_frontmatter() -> None:
    text = profile_rules.skill_text("translation-polish-zh")
    assert not text.startswith("---")
    assert "## 工作流" in text
    assert "## 翻译腔清单" in text


def test_hp_polish_uses_source_grounded_polish_skill() -> None:
    assert profile_rules.skill_name("polish") == "translation-polish-zh"
    rules = profile_rules.section_with_skill("polish", mode="轻改")
    assert "使用 translation-polish-zh skill" in rules
    assert "模式：轻改" in rules
    assert "不执行精修、重写、续写、审稿" in rules
    assert "润色模式固定为轻改" in rules
    assert "PROJECT OVERRIDES" in rules


def test_project_overrides_come_after_skill_text() -> None:
    rules = profile_rules.section_with_skill("polish", mode="轻改")
    assert rules.index("PROJECT OVERRIDES") > rules.index("## 翻译腔清单")
    assert rules.rstrip().endswith("叙述声音贴近原文：同人的语气（幽默、黑暗、浪漫、冒险）不能被润色成统一的中性网文腔。")


def test_fiction_humanizer_fork_loads() -> None:
    text = profile_rules.skill_text("fiction-humanizer-zh")
    assert "七层诊断顺序" in text
    assert "diagnostic-gates.md" in text
    assert "同一段连续两轮没有实质改善即停止" in text
