"""Series root, book sub-work and glossary master tests (ticket 01)."""

from __future__ import annotations

import json
from pathlib import Path

import book_state
import pytest


def _term(term_id: str, target: str = "", aliases=(), notes: str = "", **extra) -> dict:
    term = {
        "id": term_id,
        "source": term_id,
        "target": target,
        "aliases": list(aliases),
        "category": "person",
        "gender": "unknown",
        "confidence": "high",
        "frequency": 0,
        "evidence_refs": [],
        "notes": notes,
    }
    term.update(extra)
    return term


def _glossary(*terms: dict) -> dict:
    return {
        "version": 2,
        "high_frequency_top_n": 20,
        "applied_meta_hashes": {},
        "terms": list(terms),
    }


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _series(tmp_path: Path) -> Path:
    root = tmp_path / "series"
    book_state.create_series(
        root,
        series_id="hp-tsundere",
        title="Harry Potter and the Sorcerer's Tsundere",
        author="GuardBro",
        source_url="https://example.test/story",
        books=[
            {"number": 1, "title": "Book 1"},
            {"number": 2, "title": "Book 2"},
        ],
    )
    return root


def test_create_series_writes_metadata_and_ordered_books(tmp_path: Path) -> None:
    root = _series(tmp_path)
    data = book_state.load_series(root)
    assert data["series_id"] == "hp-tsundere"
    assert data["title"] == "Harry Potter and the Sorcerer's Tsundere"
    assert data["status"] == "in-progress"
    assert [b["number"] for b in data["books"]] == [1, 2]
    assert [b["slug"] for b in data["books"]] == ["book-01", "book-02"]
    assert (root / "glossary.json").is_file()
    assert (root / "story_bible.md").is_file()
    assert (root / "books" / "book-01" / "meta.json").is_file()


def test_series_dir_follows_the_naming_convention(tmp_path: Path) -> None:
    path = book_state.series_dir(
        tmp_path / "works", "hp-tsundere", "Harry Potter and the Sorcerer's Tsundere"
    )
    assert path == tmp_path / "works" / "hp-tsundere-harry-potter-and-the-sorcerer-s-tsundere"


def test_resolve_book_returns_a_complete_subwork(tmp_path: Path) -> None:
    root = _series(tmp_path)
    book = book_state.resolve_book(root, 2)
    assert book == root / "books" / "book-02"
    assert (book / "source").is_dir()
    assert (book / "work_temp").is_dir()
    assert (book / "translation").is_dir()
    meta = json.loads((book / "meta.json").read_text(encoding="utf-8"))
    assert meta["book_number"] == 2
    assert meta["series_id"] == "hp-tsundere"
    assert meta["title"] == "Book 2"


def test_resolve_missing_book_fails(tmp_path: Path) -> None:
    root = _series(tmp_path)
    with pytest.raises(FileNotFoundError):
        book_state.resolve_book(root, 3)


def test_resolve_ignores_a_stray_book_directory(tmp_path: Path) -> None:
    root = _series(tmp_path)
    (root / "books" / "book-03").mkdir(parents=True)
    with pytest.raises(FileNotFoundError):
        book_state.resolve_book(root, 3)


def test_create_book_adds_a_missing_book(tmp_path: Path) -> None:
    root = _series(tmp_path)
    created = book_state.create_book(root, 3, title="Book 3")
    assert book_state.resolve_book(root, 3) == created
    data = book_state.load_series(root)
    assert [b["number"] for b in data["books"]] == [1, 2, 3]
    assert data["books"][-1]["slug"] == "book-03"


def test_glossary_master_copies_into_a_book(tmp_path: Path) -> None:
    root = _series(tmp_path)
    _write(
        root / "glossary.json",
        _glossary(_term("Harry Potter", "哈利·波特", aliases=["Harry"])),
    )
    copied = book_state.copy_glossary_to_book(root, 1)
    book_glossary = json.loads(copied.read_text(encoding="utf-8"))
    assert book_glossary["terms"][0]["target"] == "哈利·波特"
    assert book_glossary["terms"][0]["source"] == "Harry Potter"


def test_copy_glossary_preserves_book_terms_and_adds_master_terms(tmp_path: Path) -> None:
    root = _series(tmp_path)
    book = book_state.resolve_book(root, 1)
    _write(
        root / "glossary.json",
        _glossary(
            _term("Harry Potter", "哈利·波特"),
            _term("Hermione Granger", "赫敏·格兰杰"),
        ),
    )
    _write(
        book / "work_temp" / "glossary.json",
        _glossary(_term("Ron", "罗恩")),
    )
    copied = book_state.copy_glossary_to_book(root, 1)
    book_glossary = json.loads(copied.read_text(encoding="utf-8"))
    assert [t["id"] for t in book_glossary["terms"]] == [
        "Ron",
        "Harry Potter",
        "Hermione Granger",
    ]


def test_merge_glossary_back_adds_and_keeps_master_target(tmp_path: Path) -> None:
    root = _series(tmp_path)
    _write(
        root / "glossary.json",
        _glossary(
            _term("Harry Potter", "哈利·波特", aliases=["Harry"], frequency=10),
        ),
    )
    book = book_state.resolve_book(root, 1)
    _write(
        book / "work_temp" / "glossary.json",
        _glossary(
            _term("Harry Potter", "哈利波特", aliases=["Potter"], notes="书内备注"),
            _term("Draconia Malfoy", "德拉科妮娅·马尔福", aliases=["Draconia"]),
        ),
    )
    result = book_state.merge_glossary_back(root, 1)
    merged = json.loads((root / "glossary.json").read_text(encoding="utf-8"))
    by_id = {t["id"]: t for t in merged["terms"]}
    assert by_id["Harry Potter"]["target"] == "哈利·波特"
    assert by_id["Harry Potter"]["aliases"] == ["Harry", "Potter"]
    assert by_id["Harry Potter"]["notes"] == "书内备注"
    assert by_id["Harry Potter"]["frequency"] == 10
    assert by_id["Draconia Malfoy"]["target"] == "德拉科妮娅·马尔福"
    assert result == {
        "added": 1,
        "updated": 1,
        "conflicts": [{"id": "Harry Potter", "master": "哈利·波特", "book": "哈利波特"}],
    }


def test_merge_glossary_back_is_idempotent(tmp_path: Path) -> None:
    root = _series(tmp_path)
    book = book_state.resolve_book(root, 1)
    book_state.copy_glossary_to_book(root, 1)
    _write(
        book / "work_temp" / "glossary.json",
        _glossary(_term("Ron", "罗恩")),
    )
    first = book_state.merge_glossary_back(root, 1)
    second = book_state.merge_glossary_back(root, 1)
    merged = json.loads((root / "glossary.json").read_text(encoding="utf-8"))
    assert [t["id"] for t in merged["terms"]] == ["Ron"]
    assert first == {"added": 1, "updated": 0, "conflicts": []}
    assert second == {"added": 0, "updated": 0, "conflicts": []}


def test_legacy_flat_work_is_not_treated_as_a_series(tmp_path: Path) -> None:
    flat = tmp_path / "legacy-work"
    flat.mkdir()
    (flat / "meta.json").write_text("{}", encoding="utf-8")
    assert book_state.work_kind(flat) == "legacy"
    assert book_state.is_series_root(flat) is False
    with pytest.raises(FileNotFoundError):
        book_state.resolve_book(flat, 1)
    assert not (flat / "series.json").exists()
    assert not (flat / "books").exists()


def test_load_glossary_terms_merges_master_and_book_with_master_target(tmp_path: Path) -> None:
    root = _series(tmp_path)
    _write(
        root / "glossary.json",
        _glossary(
            _term(
                "Kwikspell",
                "速成咒术课程",
                forbidden_targets=["快速咒语课程"],
            )
        ),
    )
    book = book_state.resolve_book(root, 1)
    _write(
        book / "work_temp" / "glossary.json",
        _glossary(
            _term("Kwikspell", "速成咒课程"),
            _term("Pawter", "波爪"),
        ),
    )
    terms = book_state.load_glossary_terms(book)
    by_id = {term["id"]: term for term in terms}
    assert by_id["Kwikspell"]["target"] == "速成咒术课程"
    assert by_id["Kwikspell"]["forbidden_targets"] == ["快速咒语课程"]
    assert by_id["Pawter"]["target"] == "波爪"
    synced = json.loads((book / "work_temp" / "glossary.json").read_text(encoding="utf-8"))
    assert {term["id"] for term in synced["terms"]} == {"Kwikspell", "Pawter"}


def test_create_book_syncs_existing_book_glossary_from_master(tmp_path: Path) -> None:
    root = _series(tmp_path)
    book = book_state.resolve_book(root, 1)
    _write(
        book / "work_temp" / "glossary.json",
        _glossary(_term("Kwikspell", "速成咒课程")),
    )
    _write(
        root / "glossary.json",
        _glossary(_term("Kwikspell", "速成咒术课程")),
    )
    book_state.create_book(root, 1, title="Book 1")
    synced = json.loads((book / "work_temp" / "glossary.json").read_text(encoding="utf-8"))
    assert synced["terms"][0]["target"] == "速成咒术课程"


def test_load_context_terms_adds_story_bible_terms_without_overriding_glossary(
    tmp_path: Path,
) -> None:
    root = _series(tmp_path)
    _write(
        root / "glossary.json",
        _glossary(_term("Draconia Malfoy", "德拉科妮娅·马尔福", aliases=["Draconia"])),
    )
    book = book_state.resolve_book(root, 1)
    bible = book / "work_temp" / "prescan" / "story_bible.md"
    bible.parent.mkdir(parents=True, exist_ok=True)
    bible.write_text(
        "\n".join(
            [
                "| 英文 | 中文 | 代词 | 备注 |",
                "|---|---|---|---|",
                "| Draconia Malfoy | 德拉科尼亚·马尔福 | 她 | 冲突时以术语表为准 |",
                "| Hedwig | 海德薇 | 它 | 哈利的猫头鹰 |",
                "| Remus Lupin | 莱姆斯·卢平 / 卢平教授 | 他 | 含斜杠，跳过 |",
                "| Fred / George Weasley | 弗雷德 / 乔治 | 他 | 多来源，跳过 |",
            ]
        ),
        encoding="utf-8",
    )
    terms = book_state.load_context_terms(book)
    by_source = {term["source"]: term["target"] for term in terms}
    assert by_source["Draconia Malfoy"] == "德拉科妮娅·马尔福"
    assert by_source["Hedwig"] == "海德薇"
    assert "Remus Lupin" not in by_source
    assert "Fred / George Weasley" not in by_source


def _series_with_global(tmp_path: Path, global_terms: list[dict]) -> Path:
    root = tmp_path / "workspace" / "series"
    book_state.create_series(
        root,
        series_id="hp-global-test",
        title="Global Test",
        author="tester",
        books=[{"number": 1, "title": "Book 1"}],
    )
    _write(
        tmp_path / "glossary" / "hp.json",
        {
            "version": 2,
            "scope": "global",
            "fandom": "Harry Potter",
            "terms": global_terms,
        },
    )
    return root


def test_global_terms_fill_series_and_book(tmp_path: Path) -> None:
    root = _series_with_global(
        tmp_path, [_term("Harry Potter", "哈利·波特", aliases=["Harry"])]
    )
    book = book_state.resolve_book(root, 1)
    terms = book_state.load_glossary_terms(book)
    by_id = {term["id"]: term for term in terms}
    assert by_id["Harry Potter"]["target"] == "哈利·波特"
    synced = json.loads((book / "work_temp" / "glossary.json").read_text(encoding="utf-8"))
    assert {term["id"] for term in synced["terms"]} == {"Harry Potter"}


def test_series_cannot_override_global_without_flag(tmp_path: Path) -> None:
    root = _series_with_global(
        tmp_path, [_term("Hospital Wing", "校医院")]
    )
    _write(
        root / "glossary.json",
        _glossary(_term("Hospital Wing", "医疗翼")),
    )
    book = book_state.resolve_book(root, 1)
    terms = book_state.load_glossary_terms(book)
    by_id = {term["id"]: term for term in terms}
    assert by_id["Hospital Wing"]["target"] == "校医院"


def test_series_can_override_global_with_flag(tmp_path: Path) -> None:
    root = _series_with_global(
        tmp_path, [_term("Hospital Wing", "校医院")]
    )
    _write(
        root / "glossary.json",
        _glossary(_term("Hospital Wing", "医疗翼", override_global=True)),
    )
    book = book_state.resolve_book(root, 1)
    terms = book_state.load_glossary_terms(book)
    by_id = {term["id"]: term for term in terms}
    assert by_id["Hospital Wing"]["target"] == "医疗翼"


def test_merge_glossary_back_ignores_global_terms(tmp_path: Path) -> None:
    root = _series_with_global(tmp_path, [_term("Harry Potter", "哈利·波特")])
    book = book_state.resolve_book(root, 1)
    _write(
        book / "work_temp" / "glossary.json",
        _glossary(_term("Harry Potter", "哈利·波特")),
    )
    result = book_state.merge_glossary_back(root, 1)
    merged = json.loads((root / "glossary.json").read_text(encoding="utf-8"))
    assert merged["terms"] == []
    assert result == {"added": 0, "updated": 0, "conflicts": []}


def test_merge_glossary_back_adds_book_only_term_over_global(tmp_path: Path) -> None:
    root = _series_with_global(tmp_path, [_term("Harry Potter", "哈利·波特")])
    book = book_state.resolve_book(root, 1)
    _write(
        book / "work_temp" / "glossary.json",
        _glossary(
            _term("Harry Potter", "哈利·波特"),
            _term("Original Character", "原创角色"),
        ),
    )
    result = book_state.merge_glossary_back(root, 1)
    merged = json.loads((root / "glossary.json").read_text(encoding="utf-8"))
    assert [term["id"] for term in merged["terms"]] == ["Original Character"]
    assert result == {"added": 1, "updated": 0, "conflicts": []}


def test_merge_glossary_back_rejects_book_override_of_global(tmp_path: Path) -> None:
    root = _series_with_global(tmp_path, [_term("Harry Potter", "哈利·波特")])
    book = book_state.resolve_book(root, 1)
    _write(
        book / "work_temp" / "glossary.json",
        _glossary(_term("Harry Potter", "哈利波特")),
    )
    result = book_state.merge_glossary_back(root, 1)
    merged = json.loads((root / "glossary.json").read_text(encoding="utf-8"))
    assert merged["terms"] == []
    assert result["conflicts"] == [
        {"id": "Harry Potter", "global": "哈利·波特", "book": "哈利波特"}
    ]


def test_merge_glossary_back_merges_forbidden_targets(tmp_path: Path) -> None:
    root = _series_with_global(tmp_path, [])
    _write(
        root / "glossary.json",
        _glossary(_term("Kwikspell", "速成咒术课程")),
    )
    book = book_state.resolve_book(root, 1)
    _write(
        book / "work_temp" / "glossary.json",
        _glossary(
            _term(
                "Kwikspell",
                "速成咒术课程",
                forbidden_targets=["快速咒语课程"],
            )
        ),
    )
    result = book_state.merge_glossary_back(root, 1)
    merged = json.loads((root / "glossary.json").read_text(encoding="utf-8"))
    by_id = {term["id"]: term for term in merged["terms"]}
    assert by_id["Kwikspell"]["forbidden_targets"] == ["快速咒语课程"]
    assert result == {"added": 0, "updated": 1, "conflicts": []}
