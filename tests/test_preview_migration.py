"""Preview migration and end-to-end pipeline tests (ticket 06).

The fixtures are synthetic legacy chunks. Translation and polish use an injected
fake client so the suite never touches the network or a live model endpoint.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import preview_migration
import pytest

HEADING_EN = "# Chapter 1: Opening"
HEADING_ZH = "# 第一章：开端"


def _payload(*targets: str) -> str:
    return json.dumps({"complete": True, "targets": list(targets)}, ensure_ascii=False)


class FakeClient:
    """Offline model boundary that answers with the prompt's segment indices."""

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        indices = [int(value) for value in re.findall(r"^\[(\d+)\]", prompt, re.MULTILINE)]
        if "Polish" in prompt:
            return _payload(*(f"润色-{index}" for index in indices))
        return _payload(*(f"译文-{index}" for index in indices))


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _legacy_work(
    tmp_path: Path,
    source_paragraphs: list[str],
    target_paragraphs: list[str],
    *,
    draft: list[str] | None = None,
    polished: list[str] | None = None,
) -> Path:
    work = tmp_path / "legacy"
    source = HEADING_EN + "\n\n" + "\n\n".join(source_paragraphs)
    _write(work / "work_temp" / "chunk0001.md", source)
    target = HEADING_ZH + "\n\n" + "\n\n".join(target_paragraphs)
    if polished is not None:
        body = HEADING_ZH + "\n\n" + "\n\n".join(polished)
        _write(work / "translation" / "polished" / "chunk0001.md", body)
    if draft is not None:
        body = HEADING_ZH + "\n\n" + "\n\n".join(draft)
        _write(work / "work_temp" / "output_chunk0001.md", body)
    if polished is None and draft is None:
        _write(work / "translation" / "polished" / "chunk0001.md", target)
    return work


def _chapter(book: Path) -> dict:
    return json.loads((book / "source" / "chapters" / "ch-01.json").read_text(encoding="utf-8"))


def _targets(chapter: dict) -> list[str | None]:
    return [segment.get("target") for segment in chapter["segments"]]


def test_migrate_resegments_the_source_and_reuses_matching_hashes(tmp_path: Path) -> None:
    work = _legacy_work(
        tmp_path,
        ["First paragraph.", "Second paragraph."],
        ["第一段。", "第二段。"],
    )
    book = tmp_path / "book"

    result = preview_migration.migrate_preview(work, ["chunk0001"], book_dir=book)

    chapter = _chapter(book)
    assert chapter["title"] == "Chapter 1: Opening"
    assert [segment["kind"] for segment in chapter["segments"]] == ["heading", "text", "text"]
    assert _targets(chapter) == ["第一章：开端", "第一段。", "第二段。"]
    assert result.reused == 3
    assert result.unmatched == 0
    assert result.chapters == [book / "source" / "chapters" / "ch-01.json"]


def test_migrate_prefers_polished_targets_over_draft_targets(tmp_path: Path) -> None:
    work = _legacy_work(
        tmp_path,
        ["First paragraph."],
        ["草稿译文。"],
        draft=["草稿译文。"],
        polished=["润色译文。"],
    )
    book = tmp_path / "book"

    result = preview_migration.migrate_preview(work, [1], book_dir=book)

    chapter = _chapter(book)
    assert _targets(chapter) == ["第一章：开端", "润色译文。"]
    assert result.reused == 2
    assert result.reused_polished == 2
    assert result.reused_draft == 0


def test_migrate_never_reuses_text_for_a_different_source_hash(tmp_path: Path) -> None:
    work = _legacy_work(
        tmp_path,
        ["First paragraph.", "Second paragraph.", "Third paragraph."],
        ["第一段。", "第二段。", "第三段。"],
    )
    changed = tmp_path / "changed-source.md"
    _write(
        changed,
        HEADING_EN + "\n\nFirst paragraph.\n\nSecond paragraph.\n\nDifferent fourth paragraph.",
    )
    book = tmp_path / "book"

    result = preview_migration.migrate_preview(work, [1], book_dir=book, source_paths=[changed])

    chapter = _chapter(book)
    assert _targets(chapter) == ["第一章：开端", "第一段。", "第二段。", None]
    assert result.reused == 3
    assert result.unmatched == 1
    assert "第三段。" not in _targets(chapter)


def test_migrate_skips_a_legacy_chunk_with_misaligned_paragraph_counts(tmp_path: Path) -> None:
    work = _legacy_work(
        tmp_path,
        ["First paragraph.", "Second paragraph.", "Third paragraph."],
        ["第一段。", "第二段。"],
    )
    book = tmp_path / "book"

    result = preview_migration.migrate_preview(work, [1], book_dir=book)

    chapter = _chapter(book)
    assert _targets(chapter) == [None, None, None, None]
    assert result.reused == 0
    assert result.unmatched == 4
    assert result.skipped_chunks == ["chunk0001"]


def test_translate_missing_only_retranslates_unmatched_segments(tmp_path: Path) -> None:
    work = _legacy_work(
        tmp_path,
        ["First paragraph.", "Second paragraph.", "Third paragraph."],
        ["第一段。", "第二段。", "第三段。"],
    )
    changed = tmp_path / "changed-source.md"
    _write(
        changed,
        HEADING_EN + "\n\nFirst paragraph.\n\nSecond paragraph.\n\nDifferent fourth paragraph.",
    )
    book = tmp_path / "book"
    preview_migration.migrate_preview(work, [1], book_dir=book, source_paths=[changed])
    client = FakeClient()

    stats = preview_migration.translate_missing(book, client)

    chapter = _chapter(book)
    assert _targets(chapter) == ["第一章：开端", "第一段。", "第二段。", "译文-3"]
    assert stats.translated == 1
    assert stats.failed == 0
    assert len(client.prompts) == 1
    assert "Different fourth paragraph." in client.prompts[0]
    assert "First paragraph." not in client.prompts[0]
    assert "Second paragraph." not in client.prompts[0]


def test_polish_pending_keeps_already_polished_targets(tmp_path: Path) -> None:
    work = _legacy_work(
        tmp_path,
        ["First paragraph.", "Second paragraph."],
        ["草稿一。", "润色二。"],
        draft=["草稿一。", "草稿二。"],
        polished=["润色一。", "润色二。"],
    )
    book = tmp_path / "book"
    # Force the second segment back to draft provenance to exercise selective polish.
    preview_migration.migrate_preview(work, [1], book_dir=book)
    chapter = _chapter(book)
    chapter["segments"][1]["target"] = "草稿一。"
    chapter["segments"][1]["target_origin"] = "draft"
    chapter["segments"][2]["target"] = "润色二。"
    chapter["segments"][2]["target_origin"] = "polished"
    preview_migration.save_chapter(book / "source" / "chapters" / "ch-01.json", chapter)
    client = FakeClient()

    stats = preview_migration.polish_pending(book, client)

    chapter = _chapter(book)
    assert _targets(chapter) == ["第一章：开端", "润色-1", "润色二。"]
    assert chapter["segments"][1]["target_origin"] == "polished"
    assert stats.polished == 1
    assert len(client.prompts) == 1
    assert "草稿一。" in client.prompts[0]
    assert "润色二。" not in client.prompts[0]


def test_render_markdown_and_html_without_paragraph_loss(tmp_path: Path) -> None:
    book = tmp_path / "book"
    chapter = {
        "chapter": 1,
        "title": "Chapter 1",
        "heading_level": 1,
        "segments": [
            {
                "index": 0,
                "kind": "heading",
                "source": "Chapter 1",
                "source_hash": "h0",
                "target": "第一章",
                "level": 1,
            },
            {
                "index": 1,
                "kind": "text",
                "source": "First.",
                "source_hash": "h1",
                "target": "第一段。",
            },
            {
                "index": 2,
                "kind": "text",
                "source": "Second.",
                "source_hash": "h2",
                "target": "第二段。",
            },
            {
                "index": 3,
                "kind": "text",
                "source": "Long first part.",
                "source_hash": "h3",
                "target": "长段前半。",
            },
            {
                "index": 4,
                "kind": "text",
                "source": "Long second part.",
                "source_hash": "h4",
                "target": "长段后半。",
                "cont": True,
            },
        ],
    }
    preview_migration.save_chapter(book / "source" / "chapters" / "ch-01.json", chapter)

    render = preview_migration.write_preview(book, output_dir=book / "translation" / "preview")
    markdown = render.markdown.read_text(encoding="utf-8")
    html = render.html.read_text(encoding="utf-8")

    assert markdown.startswith("# 第一章")
    assert "第一段。" in markdown
    assert "第二段。" in markdown
    assert "长段前半。长段后半。" in markdown
    assert len([block for block in markdown.split("\n\n") if block.strip()]) == 4
    assert "第一段。" in html
    assert "第二段。" in html
    assert "长段前半。长段后半。" in html
    assert "<h1" in html and "第一章" in html
    assert "FangSong" in html
    assert "toc-toggle" in html
    assert "目录" in html


def test_run_preview_end_to_end_produces_markdown_and_html(tmp_path: Path) -> None:
    work = _legacy_work(
        tmp_path,
        ["First paragraph.", "Second paragraph.", "Third paragraph."],
        ["第一段。", "第二段。", "第三段。"],
    )
    changed = tmp_path / "changed-source.md"
    _write(
        changed,
        HEADING_EN + "\n\nFirst paragraph.\n\nSecond paragraph.\n\nDifferent fourth paragraph.",
    )
    book = tmp_path / "book"
    client = FakeClient()

    result = preview_migration.run_preview(
        work,
        [1],
        book_dir=book,
        source_paths=[changed],
        client=client,
        output_dir=book / "translation" / "preview",
    )

    chapter = _chapter(book)
    assert _targets(chapter) == ["第一章：开端", "第一段。", "第二段。", "润色-3"]
    assert result.migration.reused == 3
    assert result.migration.unmatched == 1
    assert result.translation is not None
    assert result.translation.translated == 1
    assert result.polish is not None
    assert result.polish.polished == 1
    assert result.markdown.is_file()
    assert result.html.is_file()
    markdown = result.markdown.read_text(encoding="utf-8")
    html = result.html.read_text(encoding="utf-8")
    assert "第一段。" in markdown
    assert "第二段。" in markdown
    assert "润色-3" in markdown
    assert "润色-3" in html
    assert "FangSong" in html
    assert "toc-toggle" in html


def test_run_preview_refuses_to_render_untranslated_segments(tmp_path: Path) -> None:
    work = _legacy_work(
        tmp_path,
        ["First paragraph.", "Second paragraph."],
        ["第一段。", "第二段。"],
    )
    changed = tmp_path / "changed-source.md"
    _write(changed, HEADING_EN + "\n\nFirst paragraph.\n\nDifferent third paragraph.")
    book = tmp_path / "book"

    with pytest.raises(ValueError, match="untranslated"):
        preview_migration.run_preview(work, [1], book_dir=book, source_paths=[changed], client=None)


def test_migrate_archives_stale_chapters_under_book_archive(tmp_path: Path) -> None:
    work = _legacy_work(
        tmp_path,
        ["First paragraph."],
        ["第一段。"],
    )
    book = tmp_path / "book"
    stale = book / "source" / "chapters" / "ch-99.json"
    _write(stale, json.dumps({"chapter": 99, "segments": []}))

    preview_migration.migrate_preview(work, [1], book_dir=book)

    assert not stale.exists()
    archived = list((book / "archive").rglob("ch-99.json"))
    assert len(archived) == 1
    assert archived[0].parent.name == "chapters"
    assert "source/chapters/archive" not in str(archived[0])


def test_run_preview_injects_glossary_and_neighbour_context(tmp_path: Path) -> None:
    work = _legacy_work(
        tmp_path,
        ["Draco waited."],
        ["德拉科等待着。"],
    )
    changed = tmp_path / "changed-source.md"
    _write(
        changed,
        HEADING_EN + "\n\nDraco waited.\n\nDraco left the alley.",
    )
    book = tmp_path / "book"
    client = FakeClient()

    preview_migration.run_preview(
        work,
        [1],
        book_dir=book,
        source_paths=[changed],
        client=client,
        output_dir=book / "translation" / "preview",
        glossary_terms=[
            {"source": "Draco", "target": "德拉科"},
            {"source": "Harry", "target": "哈利"},
        ],
        context="Previous chunk excerpt: Draco met Harry.",
    )

    prompt = client.prompts[0]
    assert "Draco => 德拉科" in prompt
    assert "Harry => 哈利" not in prompt
    assert "Previous chunk excerpt: Draco met Harry." in prompt
