"""HP fanfic profile and prescan/review helper tests."""

from __future__ import annotations

from pathlib import Path

import build_prescan
import prompt_profile
import review_book


def test_profile_loads_manifest() -> None:
    manifest = prompt_profile.load_profile()
    assert manifest["id"] == "hp-fanfic"
    assert "translation" in manifest["files"]
    assert manifest["female_draco"]["short"] == "德拉科妮娅"


def test_profile_sections_are_present_and_specific() -> None:
    translation = prompt_profile.section("translation")
    assert "德拉科妮娅" in translation
    assert "达西" in translation
    assert "咒语" in translation
    story = prompt_profile.section("story_bible")
    assert "秘密与知情范围" in story
    review = prompt_profile.section("book_review")
    assert "跨章" in review


def test_profile_compose_and_forbidden() -> None:
    composed = prompt_profile.compose("BASE", "polish")
    assert composed.startswith("BASE")
    assert "哈利波特" in composed
    assert "达西" in prompt_profile.forbidden_names()


def test_chapter_files_reads_numbered_chapters(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "work.md").write_text(
        "# Chapter 1: Start\nA\n\n# Chapter 2: Next\nB\n", encoding="utf-8"
    )
    chapters = build_prescan.chapter_files(tmp_path)
    assert [n for n, _, _ in chapters] == [1, 2]
    assert chapters[1][1] == "Next"


def test_chunk_map_maps_chunks_to_chapters(tmp_path: Path) -> None:
    (tmp_path / "chunk0001.md").write_text("# Chapter 1\nA", encoding="utf-8")
    (tmp_path / "chunk0002.md").write_text("continued", encoding="utf-8")
    (tmp_path / "chunk0003.md").write_text("# Chapter 2\nB", encoding="utf-8")
    assert build_prescan.chunk_map(tmp_path) == {
        "chunk0001": 1,
        "chunk0002": 1,
        "chunk0003": 2,
    }


def test_prescan_sha_is_content_addressed() -> None:
    assert build_prescan.sha("abc") == build_prescan.sha("abc")
    assert build_prescan.sha("abc") != build_prescan.sha("abd")


def test_deterministic_issues_flags_forbidden_and_male_draco() -> None:
    profile = prompt_profile.load_profile()
    terms = [{"source": "Darcy", "target": "德拉科妮娅", "aliases": ["Draco"]}]
    source = "Darcy Malfoy looked at Harry."
    target = "达西看着哈利，德拉科冷笑。"
    issues = review_book.deterministic_issues(source, target, terms, profile)
    details = " ".join(i["detail"] for i in issues)
    assert "达西" in details
    assert "德拉科" in details


def test_deterministic_issues_accepts_full_female_name() -> None:
    profile = prompt_profile.load_profile()
    source = "Darcy smiled."
    target = "德拉科妮娅笑了。"
    issues = review_book.deterministic_issues(source, target, [], profile)
    assert not any("德拉科" in i["detail"] for i in issues)


def test_deterministic_issues_flags_structure_and_term_drift() -> None:
    profile = prompt_profile.load_profile()
    terms = [{"source": "Hogwarts", "target": "霍格沃茨", "aliases": []}]
    source = "Hogwarts.\n\nSecond.\n\nThird.\n\nFourth."
    target = "霍格沃茨。"
    issues = review_book.deterministic_issues(source, target, terms, profile)
    assert any(i["type"] == "structure" for i in issues)
    assert not any("霍格沃茨" in i["detail"] for i in issues)


def test_group_conflicts_needs_two_chunks_and_values() -> None:
    issue_a = {
        "severity": "一般",
        "type": "pronoun",
        "consistency": {
            "subject_source": "Darcy",
            "kind": "pronoun",
            "proposed_value": "她",
        },
    }
    issue_b = {
        "severity": "一般",
        "type": "pronoun",
        "consistency": {
            "subject_source": "Darcy",
            "kind": "pronoun",
            "proposed_value": "他",
        },
    }
    conflicts = review_book.group_conflicts({"chunk0001": [issue_a], "chunk0002": [issue_b]})
    assert len(conflicts) == 1
    assert conflicts[0]["values"] == ["他", "她"]


def test_report_and_merge_verdicts(tmp_path: Path) -> None:
    issues = [
        {
            "severity": "严重",
            "type": "knowledge",
            "detail": "提前知道",
            "translation": "她知道",
            "source": "she did not know",
            "chapters": "第 1 章 → 第 5 章",
            "suggestion": "改为不知道",
        }
    ]
    report = review_book.report_markdown("chunk0001", issues)
    assert "结论：FAIL" in report
    assert "第 1 章 → 第 5 章" in report
    merged = review_book.merge_book_report(
        tmp_path, {"chunk0001": report}, [], {"chunk0001": issues}
    )
    assert "FAIL 块：chunk0001" in merged
    assert "跨章冲突" in merged
