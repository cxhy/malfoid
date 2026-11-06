"""Preview pipeline context injection tests."""

from __future__ import annotations

import json
from pathlib import Path

import preview_pipeline


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_load_glossary_terms_reads_the_work_glossary(tmp_path: Path) -> None:
    work = tmp_path / "work"
    _write(
        work / "work_temp" / "glossary.json",
        json.dumps(
            {"terms": [{"source": "Draco", "target": "德拉科"}, "not-a-term"]},
            ensure_ascii=False,
        ),
    )
    assert preview_pipeline.load_glossary_terms(work) == [{"source": "Draco", "target": "德拉科"}]


def test_load_glossary_terms_returns_empty_without_a_glossary(tmp_path: Path) -> None:
    assert preview_pipeline.load_glossary_terms(tmp_path / "missing") == []


def test_build_neighbour_context_uses_chunks_around_the_selection(tmp_path: Path) -> None:
    work = tmp_path / "work"
    _write(work / "work_temp" / "chunk0001.md", "Previous chapter text.")
    _write(work / "work_temp" / "chunk0002.md", "Selected chapter text.")
    _write(work / "work_temp" / "chunk0003.md", "Next chapter text.")

    context = preview_pipeline.build_neighbour_context(work, ["2"])

    assert "Previous chunk chunk0001 excerpt:" in context
    assert "Previous chapter text." in context
    assert "Next chunk chunk0003 excerpt:" in context
    assert "Next chapter text." in context
    assert "Selected chapter text." not in context


def test_build_neighbour_context_is_empty_without_neighbours(tmp_path: Path) -> None:
    work = tmp_path / "work"
    _write(work / "work_temp" / "chunk0001.md", "Only chapter text.")
    assert preview_pipeline.build_neighbour_context(work, ["1"]) == ""
