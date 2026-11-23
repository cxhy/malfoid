"""Deterministic glossary alignment tests."""

from __future__ import annotations

import json
from pathlib import Path

import align_glossary
import segment_state


def _segment(index: int, source: str, target: str) -> dict:
    return {
        "index": index,
        "kind": "text",
        "source": source,
        "source_hash": segment_state.source_hash(source),
        "target": target,
    }


def _term(
    term_id: str,
    source: str,
    target: str,
    *,
    aliases: list[str] | None = None,
    forbidden: list[str] | None = None,
) -> dict:
    return {
        "id": term_id,
        "source": source,
        "target": target,
        "aliases": aliases or [],
        "category": "person",
        "confidence": "high",
        "forbidden_targets": forbidden or [],
    }


def _book(
    tmp_path: Path, segments: list[dict], terms: list[dict], *, book: Path | None = None
) -> Path:
    book = book or tmp_path / "book"
    (book / "meta.json").parent.mkdir(parents=True, exist_ok=True)
    (book / "meta.json").write_text("{}", encoding="utf-8")
    chapter = {
        "chapter": 1,
        "title": "Chapter One",
        "heading_level": 1,
        "segments": [
            {
                "index": 0,
                "kind": "heading",
                "source": "Chapter One",
                "source_hash": segment_state.source_hash("Chapter One"),
                "target": "第一章",
            },
            *segments,
        ],
    }
    segment_state.save_chapter(segment_state.chapter_path(book, 1), chapter)
    glossary = {
        "version": 2,
        "high_frequency_top_n": 20,
        "applied_meta_hashes": {},
        "terms": terms,
    }
    path = book / "work_temp" / "glossary.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(glossary, ensure_ascii=False), encoding="utf-8")
    return book


def test_alignment_accepts_canonical_component(tmp_path: Path) -> None:
    book = _book(
        tmp_path,
        [_segment(1, "Alyza waited.", "阿莉莎等待着。")],
        [
            _term(
                "Alyza Twigs",
                "Alyza Twigs",
                "阿莉莎·特威格斯",
                aliases=["Alyza"],
                forbidden=["艾莉莎", "阿莉扎"],
            )
        ],
    )
    report = align_glossary.scan_alignment(book)
    assert report["errors"] == []
    assert report["warnings"] == []


def test_alignment_reports_forbidden_variant(tmp_path: Path) -> None:
    book = _book(
        tmp_path,
        [_segment(1, "Alyza waited.", "艾莉莎等待着。")],
        [
            _term(
                "Alyza Twigs",
                "Alyza Twigs",
                "阿莉莎·特威格斯",
                aliases=["Alyza"],
                forbidden=["艾莉莎"],
            )
        ],
    )
    report = align_glossary.scan_alignment(book)
    assert report["errors"]
    assert report["errors"][0]["kind"] == "glossary_variant"
    assert report["errors"][0]["variant"] == "艾莉莎"


def test_alignment_reports_canonical_missing_as_warning(tmp_path: Path) -> None:
    book = _book(
        tmp_path,
        [_segment(1, "Alyza waited.", "她等待着。")],
        [_term("Alyza Twigs", "Alyza Twigs", "阿莉莎·特威格斯", aliases=["Alyza"])],
    )
    report = align_glossary.scan_alignment(book)
    assert report["errors"] == []
    assert report["warnings"]
    assert report["warnings"][0]["kind"] == "canonical_missing"


def test_coverage_reports_uncovered_proper_noun(tmp_path: Path) -> None:
    book = _book(
        tmp_path,
        [
            _segment(1, "Alyza waited.", "阿莉莎等待着。"),
            _segment(2, "Alyza smiled.", "阿莉莎笑了。"),
        ],
        [_term("Harry", "Harry", "哈利")],
    )
    report = align_glossary.coverage_report(book, min_count=2)
    assert [item["candidate"] for item in report["missing"]] == ["Alyza"]
    assert report["missing"][0]["count"] == 2


def test_coverage_ignores_covered_names_and_common_words(tmp_path: Path) -> None:
    book = _book(
        tmp_path,
        [
            _segment(1, "Harry waited.", "哈利等待着。"),
            _segment(2, "The boy waited.", "男孩等待着。"),
        ],
        [_term("Harry", "Harry", "哈利")],
    )
    report = align_glossary.coverage_report(book, min_count=2)
    assert report["missing"] == []


def test_discover_variants_suggests_chinese_candidates(tmp_path: Path) -> None:
    book = _book(
        tmp_path,
        [
            _segment(1, "Alyza waited.", "艾莉莎等待着。"),
            _segment(2, "Alyza smiled.", "艾莉莎笑了。"),
            _segment(3, "Alyza left.", "艾莉莎离开了。"),
        ],
        [_term("Alyza Twigs", "Alyza Twigs", "阿莉莎·特威格斯", aliases=["Alyza"])],
    )
    suggestions = align_glossary.discover_variants(book, min_count=2)
    candidates = {item["candidate"] for item in suggestions}
    assert "艾莉莎" in candidates


def test_apply_forbidden_replacements_uses_component(tmp_path: Path) -> None:
    book = _book(
        tmp_path,
        [_segment(1, "Alyza waited.", "艾莉莎等待着。")],
        [
            _term(
                "Alyza Twigs",
                "Alyza Twigs",
                "阿莉莎·特威格斯",
                aliases=["Alyza"],
                forbidden=["艾莉莎"],
            )
        ],
    )
    result = align_glossary.apply_forbidden_replacements(book, dry_run=False)
    assert result["changed_segments"] == 1
    chapter = segment_state.load_chapter(segment_state.chapter_path(book, 1))
    assert chapter["segments"][1]["target"] == "阿莉莎等待着。"


def test_apply_forbidden_replacements_uses_full_name_for_separator(tmp_path: Path) -> None:
    book = _book(
        tmp_path,
        [_segment(1, "Alyza Twigs waited.", "艾莉莎·特威格斯等待着。")],
        [
            _term(
                "Alyza Twigs",
                "Alyza Twigs",
                "阿莉莎·特威格斯",
                aliases=["Alyza"],
                forbidden=["艾莉莎·特威格斯"],
            )
        ],
    )
    result = align_glossary.apply_forbidden_replacements(book, dry_run=False)
    assert result["changed_segments"] == 1
    chapter = segment_state.load_chapter(segment_state.chapter_path(book, 1))
    assert chapter["segments"][1]["target"] == "阿莉莎·特威格斯等待着。"


def test_strict_coverage_turns_missing_names_into_errors(tmp_path: Path) -> None:
    book = _book(
        tmp_path,
        [
            _segment(1, "Alyza waited.", "阿莉莎等待着。"),
            _segment(2, "Alyza smiled.", "阿莉莎笑了。"),
        ],
        [_term("Harry", "Harry", "哈利")],
    )
    result = align_glossary.report(book, strict_coverage=True, min_count=2)
    assert result["ok"] is False
    assert any(item["kind"] == "glossary_coverage" for item in result["errors"])


def test_alignment_accepts_a_canonical_suffix_prefix(tmp_path: Path) -> None:
    book = _book(
        tmp_path,
        [_segment(1, "The Weasleys waited.", "韦斯莱家等待着。")],
        [_term("Weasleys", "Weasleys", "韦斯莱一家")],
    )
    report = align_glossary.scan_alignment(book)
    assert report["errors"] == []
    assert report["warnings"] == []


def test_alignment_accepts_an_explicit_canonical_form(tmp_path: Path) -> None:
    term = _term("Ministry", "Ministry", "魔法部")
    term["canonical_forms"] = ["部里"]
    book = _book(
        tmp_path,
        [_segment(1, "The Ministry waited.", "部里等待着。")],
        [term],
    )
    report = align_glossary.scan_alignment(book)
    assert report["errors"] == []
    assert report["warnings"] == []


def test_source_scoped_variant_ignores_unrelated_source(tmp_path: Path) -> None:
    term = _term("Great Hall", "Great Hall", "礼堂", forbidden=["大厅"])
    term["forbidden_source_scoped"] = True
    book = _book(
        tmp_path,
        [_segment(1, "The hall was empty.", "大厅空着。")],
        [term],
    )
    report = align_glossary.scan_alignment(book)
    assert report["errors"] == []
    assert report["warnings"] == []


def test_source_scoped_variant_reports_when_source_matches(tmp_path: Path) -> None:
    term = _term("Great Hall", "Great Hall", "礼堂", forbidden=["大厅"])
    term["forbidden_source_scoped"] = True
    book = _book(
        tmp_path,
        [_segment(1, "The Great Hall was empty.", "大厅空着。")],
        [term],
    )
    report = align_glossary.scan_alignment(book)
    assert report["errors"]
    assert report["errors"][0]["variant"] == "大厅"


def test_apply_forbidden_replacements_requires_source_match(tmp_path: Path) -> None:
    term = _term("Great Hall", "Great Hall", "礼堂", forbidden=["大厅"])
    term["forbidden_source_scoped"] = True
    book = _book(
        tmp_path,
        [
            _segment(1, "The Great Hall was empty.", "大厅空着。"),
            _segment(2, "The hall was empty.", "大厅空着。"),
        ],
        [term],
    )
    result = align_glossary.apply_forbidden_replacements(book, dry_run=False)
    assert result["changed_segments"] == 1
    chapter = segment_state.load_chapter(segment_state.chapter_path(book, 1))
    assert chapter["segments"][1]["target"] == "礼堂空着。"
    assert chapter["segments"][2]["target"] == "大厅空着。"


def test_forbidden_variant_inside_another_canonical_is_ignored(tmp_path: Path) -> None:
    terms = [
        _term("Draco", "Draco", "德拉科妮娅", aliases=["Dracina"], forbidden=["德拉科"]),
        _term("Dracina Malfoy", "Dracina Malfoy", "德拉科妮娅·马尔福", forbidden=["德拉科"]),
    ]
    book = _book(
        tmp_path,
        [_segment(1, "Dracina Malfoy waited.", "德拉科妮娅·马尔福等待着。")],
        terms,
    )
    report = align_glossary.scan_alignment(book)
    assert report["errors"] == []


def test_shared_alias_keeps_both_terms_and_protects_canonical(tmp_path: Path) -> None:
    terms = [
        _term(
            "Avada Kedavra",
            "Avada Kedavra",
            "阿瓦达索命",
            aliases=["Killing Curse"],
            forbidden=["索命咒"],
        ),
        _term("Killing Curse", "Killing Curse", "阿瓦达索命咒"),
    ]
    index = align_glossary.build_alignment_terms(terms)
    assert {term.term_id for term in index} == {"Avada Kedavra", "Killing Curse"}
    book = _book(
        tmp_path,
        [_segment(1, "The Killing Curse hit him.", "阿瓦达索命咒击中了他。")],
        terms,
    )
    report = align_glossary.scan_alignment(book)
    assert report["errors"] == []


def test_apply_forbidden_replacements_leaves_ambiguous_component_unresolved(tmp_path: Path) -> None:
    book = _book(
        tmp_path,
        [_segment(1, "Alyza waited.", "丙丙丙等待着。")],
        [
            _term(
                "Alyza Twigs",
                "Alyza Twigs",
                "阿阿·特特特特",
                aliases=["Alyza"],
                forbidden=["丙丙丙"],
            )
        ],
    )
    result = align_glossary.apply_forbidden_replacements(book, dry_run=False)
    assert result["changed_segments"] == 0
    assert result["unresolved"]
    chapter = segment_state.load_chapter(segment_state.chapter_path(book, 1))
    assert chapter["segments"][1]["target"] == "丙丙丙等待着。"


def test_apply_forbidden_replacements_does_not_duplicate_canonical_prefix(tmp_path: Path) -> None:
    book = _book(
        tmp_path,
        [_segment(1, "Peter Pettigrew waited.", "小彼得·佩迪鲁等待着。")],
        [
            _term(
                "Peter Pettigrew",
                "Peter Pettigrew",
                "小矮星彼得",
                aliases=["Peter"],
                forbidden=["彼得·佩迪鲁"],
            )
        ],
    )
    result = align_glossary.apply_forbidden_replacements(book, dry_run=False)
    assert result["changed_segments"] == 1
    chapter = segment_state.load_chapter(segment_state.chapter_path(book, 1))
    assert chapter["segments"][1]["target"] == "小矮星彼得等待着。"


def test_restore_targets_from_archive_requires_index_and_source_hash(tmp_path: Path) -> None:
    book = _book(
        tmp_path,
        [_segment(1, "Alyza waited.", "错误译文。")],
        [],
    )
    archive = tmp_path / "archive"
    chapter = {
        "chapter": 1,
        "title": "Chapter One",
        "heading_level": 1,
        "segments": [
            {
                "index": 1,
                "kind": "text",
                "source": "Alyza waited.",
                "source_hash": segment_state.source_hash("Alyza waited."),
                "target": "阿莉莎等待着。",
            },
            {
                "index": 2,
                "kind": "text",
                "source": "Old source.",
                "source_hash": segment_state.source_hash("Old source."),
                "target": "旧译文。",
            },
        ],
    }
    path = archive / "source" / "chapters" / "ch-01.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(chapter, ensure_ascii=False), encoding="utf-8")
    result = align_glossary.restore_targets_from_archive(book, archive)
    assert result["restored"] == 1
    current = segment_state.load_chapter(segment_state.chapter_path(book, 1))
    assert current["segments"][1]["target"] == "阿莉莎等待着。"
    assert current["segments"][0]["target"] == "第一章"


def test_coverage_ignore_skips_series_common_words(tmp_path: Path) -> None:
    series = tmp_path / "series"
    book = series / "books" / "book-01"
    _book(
        tmp_path,
        [_segment(1, "Honestly, Zorba waited.", "说实话，佐巴等待着。")],
        [],
        book=book,
    )
    (series / "glossary.json").write_text(
        json.dumps({"terms": [], "coverage_ignore": ["Honestly"]}),
        encoding="utf-8",
    )
    report = align_glossary.coverage_report(book, min_count=1)
    assert [item["candidate"] for item in report["missing"]] == ["Zorba"]
