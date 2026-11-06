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
