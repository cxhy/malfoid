"""AI-assisted glossary alignment tests (offline)."""

from __future__ import annotations

import json
from pathlib import Path

import align_glossary
import book_state
import glossary_align_ai
import pytest
import segment_state
import translate_book_v2


class DecisionClient:
    def __init__(self, decisions: list[dict]) -> None:
        self.decisions = decisions
        self.calls: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.calls.append(prompt)
        return json.dumps({"decisions": self.decisions}, ensure_ascii=False)


def _book(
    tmp_path: Path,
    source: str = "Zorba waited.",
    target: str = "佐巴等待着。",
    repeat: int = 1,
) -> Path:
    root = tmp_path / "series"
    book_state.create_series(
        root,
        series_id="hp",
        title="Test",
        author="A",
        books=[{"number": 1, "title": "Book 1"}],
    )
    book = book_state.resolve_book(root, 1)
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
            *[
                {
                    "index": index + 1,
                    "kind": "text",
                    "source": source,
                    "source_hash": segment_state.source_hash(source),
                    "target": target,
                }
                for index in range(repeat)
            ],
        ],
    }
    segment_state.save_chapter(segment_state.chapter_path(book, 1), chapter)
    return book


def _term(term_id: str, source: str, target: str, aliases: list[str] | None = None) -> dict:
    return {
        "id": term_id,
        "source": source,
        "target": target,
        "aliases": aliases or [],
        "category": "person",
        "confidence": "high",
        "evidence_refs": [],
        "frequency": 0,
        "notes": "",
    }


def _set_master(book: Path, terms: list[dict], coverage_ignore: list[str] | None = None) -> None:
    path = book.parent.parent / "glossary.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["terms"] = terms
    if coverage_ignore is not None:
        data["coverage_ignore"] = coverage_ignore
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    book_state.sync_glossary(book)


def test_ai_alignment_adds_new_series_term(tmp_path: Path) -> None:
    book = _book(tmp_path)
    client = DecisionClient(
        [
            {
                "candidate": "Zorba",
                "action": "new",
                "source": "Zorba",
                "target": "佐巴",
                "canonical_forms": ["佐巴"],
                "category": "person",
            }
        ]
    )
    result = glossary_align_ai.align_book(book, client, min_count=1)
    assert result["applied"] is True
    assert result["coverage_missing"] == 0
    terms = book_state.load_glossary_terms(book)
    assert any(term["id"] == "Zorba" and term["target"] == "佐巴" for term in terms)
    master = json.loads((book.parent.parent / "glossary.json").read_text(encoding="utf-8"))
    assert any(term["id"] == "Zorba" for term in master["terms"])
    assert not (book.parent.parent.parent / "glossary" / "hp.json").exists()


def test_ai_alignment_adds_alias_to_existing_term(tmp_path: Path) -> None:
    book = _book(tmp_path, source="McGongall waited.", target="麦格等待着。")
    _set_master(book, [_term("McGonagall", "McGonagall", "麦格")])
    client = DecisionClient(
        [{"candidate": "McGongall", "action": "alias", "term_id": "McGonagall"}]
    )
    result = glossary_align_ai.align_book(book, client, min_count=1)
    assert result["coverage_missing"] == 0
    terms = book_state.load_glossary_terms(book)
    mcgonagall = next(term for term in terms if term["id"] == "McGonagall")
    assert "McGongall" in mcgonagall["aliases"]


def test_ai_alignment_ignores_common_candidate(tmp_path: Path) -> None:
    book = _book(tmp_path, source="Honestly, Zorba waited.", target="说实话，佐巴等待着。")
    client = DecisionClient(
        [
            {"candidate": "Honestly", "action": "ignore"},
            {"candidate": "Zorba", "action": "ignore"},
        ]
    )
    result = glossary_align_ai.align_book(book, client, min_count=1)
    assert result["coverage_missing"] == 0
    master = json.loads((book.parent.parent / "glossary.json").read_text(encoding="utf-8"))
    assert "Honestly" in master["coverage_ignore"]
    assert "Zorba" in master["coverage_ignore"]


def test_ai_alignment_rejects_unresolved_without_writing(tmp_path: Path) -> None:
    book = _book(tmp_path)
    before = (book.parent.parent / "glossary.json").read_text(encoding="utf-8")
    client = DecisionClient([{"candidate": "Zorba", "action": "unresolved"}])
    with pytest.raises(glossary_align_ai.GlossaryAlignmentError):
        glossary_align_ai.align_book(book, client, min_count=1)
    assert (book.parent.parent / "glossary.json").read_text(encoding="utf-8") == before


def test_candidate_context_includes_the_name(tmp_path: Path) -> None:
    filler = "The corridor was quiet. " * 30
    book = _book(tmp_path, source=filler + "Zorba waited.")
    contexts = glossary_align_ai._contexts(book, "Zorba")
    assert contexts
    assert "Zorba" in contexts[0]["source"]


def test_ai_alignment_retries_omitted_candidates(tmp_path: Path) -> None:
    book = _book(tmp_path)

    class OmittingClient:
        def __init__(self) -> None:
            self.calls = 0

        def __call__(self, prompt: str) -> str:
            self.calls += 1
            if self.calls == 1:
                return json.dumps({"decisions": []})
            return json.dumps(
                {
                    "decisions": [
                        {
                            "candidate": "Zorba",
                            "action": "new",
                            "source": "Zorba",
                            "target": "佐巴",
                            "category": "person",
                        }
                    ]
                },
                ensure_ascii=False,
            )

    client = OmittingClient()
    result = glossary_align_ai.align_book(book, client, min_count=1)
    assert client.calls == 2
    assert result["coverage_missing"] == 0
    terms = book_state.load_glossary_terms(book)
    assert any(term["id"] == "Zorba" for term in terms)


def test_ai_alignment_rejects_english_target(tmp_path: Path) -> None:
    book = _book(tmp_path)
    client = DecisionClient(
        [{"candidate": "Zorba", "action": "new", "source": "Zorba", "target": "Zorba"}]
    )
    with pytest.raises(glossary_align_ai.GlossaryAlignmentError):
        glossary_align_ai.align_book(book, client, min_count=1)


class TranslationClient:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.calls.append(prompt)
        if "Return JSON only:" in prompt:
            return json.dumps(
                {
                    "decisions": [
                        {
                            "candidate": "Zorba",
                            "action": "new",
                            "source": "Zorba",
                            "target": "佐巴",
                            "canonical_forms": ["佐巴"],
                            "category": "person",
                        }
                    ]
                },
                ensure_ascii=False,
            )
        return json.dumps(
            {"complete": True, "targets": ["佐巴等待着。", "佐巴等待着。", "佐巴等待着。"]},
            ensure_ascii=False,
        )


def test_translate_book_aligns_before_translation(tmp_path: Path) -> None:
    book = _book(tmp_path, source="Zorba waited.", target="", repeat=3)
    client = TranslationClient()
    summary = translate_book_v2.translate_book(book, client, polish=False)
    assert summary["glossary_alignment"]["coverage_missing"] == 0
    assert summary["glossary_coverage_missing"] == []
    assert summary["translated"] == 3
    assert any("Return JSON only:" in call for call in client.calls)
    chapter = segment_state.load_chapter(segment_state.chapter_path(book, 1))
    assert chapter["segments"][1]["target"] == "佐巴等待着。"
    assert chapter["segments"][3]["target"] == "佐巴等待着。"


def test_translate_book_blocks_when_alignment_unresolved(tmp_path: Path) -> None:
    book = _book(tmp_path, source="Zorba waited.", target="", repeat=3)
    client = DecisionClient([{"candidate": "Zorba", "action": "unresolved"}])
    with pytest.raises(glossary_align_ai.GlossaryAlignmentError):
        translate_book_v2.translate_book(book, client, polish=False)


def test_apply_alias_to_global_only_term(tmp_path: Path) -> None:
    root = tmp_path / "works" / "series"
    book_state.create_series(
        root,
        series_id="hp",
        title="Test",
        author="A",
        books=[{"number": 1, "title": "Book 1"}],
    )
    book = book_state.resolve_book(root, 1)
    global_dir = tmp_path / "glossary"
    global_dir.mkdir()
    (global_dir / "hp.json").write_text(
        json.dumps(
            {
                "version": 2,
                "terms": [_term("Gregory Goyle", "Gregory Goyle", "格雷戈里·高尔")],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    result = glossary_align_ai._apply(
        book,
        [{"term_id": "Gregory Goyle", "candidate": "Gregory"}],
        [],
        [],
        False,
    )
    assert result["applied"] is True
    master = json.loads((root / "glossary.json").read_text(encoding="utf-8"))
    term = next(term for term in master["terms"] if term["id"] == "Gregory Goyle")
    assert term["target"] == "格雷戈里·高尔"
    assert "Gregory" in term["aliases"]
    merged = book_state.load_glossary_terms(book)
    merged_term = next(term for term in merged if term["id"] == "Gregory Goyle")
    assert "Gregory" in merged_term["aliases"]


def test_apply_alias_to_book_only_term(tmp_path: Path) -> None:
    root = tmp_path / "series"
    book_state.create_series(
        root,
        series_id="hp",
        title="Test",
        author="A",
        books=[{"number": 1, "title": "Book 1"}],
    )
    book = book_state.resolve_book(root, 1)
    book_glossary = book / "work_temp" / "glossary.json"
    data = json.loads(book_glossary.read_text(encoding="utf-8"))
    data.setdefault("terms", []).append(
        _term("Zorba", "Zorba", "佐巴", aliases=["Zorb"])
    )
    book_glossary.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    result = glossary_align_ai._apply(
        book,
        [{"term_id": "Zorba", "candidate": "Zorba the Greek"}],
        [],
        [],
        False,
    )
    assert result["applied"] is True
    master = json.loads((root / "glossary.json").read_text(encoding="utf-8"))
    term = next(term for term in master["terms"] if term["id"] == "Zorba")
    assert term["target"] == "佐巴"
    assert "Zorba the Greek" in term["aliases"]
    merged = book_state.load_glossary_terms(book)
    merged_term = next(term for term in merged if term["id"] == "Zorba")
    assert "Zorba the Greek" in merged_term["aliases"]
