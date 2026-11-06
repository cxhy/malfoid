"""Report bookkeeping and incremental-update helpers."""

from __future__ import annotations

from pathlib import Path

import incremental_update
import proofread_all


def test_verdict_parsing(tmp_path: Path) -> None:
    report = tmp_path / "chunk0001.md"
    report.write_text("## 严重\n\n- 无\n\n结论：PASS\n", encoding="utf-8")
    assert proofread_all.verdict_of(report) == "PASS"
    report.write_text("结论：FAIL\n", encoding="utf-8")
    assert proofread_all.verdict_of(report) == "FAIL"
    report.write_text("没有结论\n", encoding="utf-8")
    assert proofread_all.verdict_of(report) == "UNKNOWN"
    assert proofread_all.verdict_of(tmp_path / "missing.md") == "MISSING"


def test_issue_count(tmp_path: Path) -> None:
    report = tmp_path / "chunk0001.md"
    report.write_text(
        "## 严重\n- 【误译】a\n- 【漏译】b\n## 一般\n- 【词义】c\n",
        encoding="utf-8",
    )
    assert proofread_all.issue_count(report) == 3


def test_pending_chunks(tmp_path: Path) -> None:
    work = tmp_path / "work"
    temp = work / "work_temp"
    temp.mkdir(parents=True)
    (work / "translation" / "proofread").mkdir(parents=True)
    for cid in ("chunk0001", "chunk0002"):
        (temp / f"{cid}.md").write_text("x", encoding="utf-8")
    (temp / "output_chunk0001.md").write_text("x", encoding="utf-8")
    (work / "translation" / "proofread" / "chunk0001.md").write_text(
        "## 严重\n\n无\n\n结论：PASS\n", encoding="utf-8"
    )
    assert proofread_all.chunk_ids(work) == ["chunk0001", "chunk0002"]
    assert proofread_all.pending(work) == ["chunk0002"]


def test_sha_is_stable_and_content_addressed() -> None:
    assert incremental_update.sha("abc") == incremental_update.sha("abc")
    assert incremental_update.sha("abc") != incremental_update.sha("abd")
    assert len(incremental_update.sha("abc")) == 64


def test_detect_html(tmp_path: Path) -> None:
    fic = tmp_path / "fic.html"
    fic.write_text('<div id="chap_1">x</div>', encoding="utf-8")
    ao3 = tmp_path / "ao3.html"
    ao3.write_text('<div id="chapters">x</div>', encoding="utf-8")
    other = tmp_path / "other.html"
    other.write_text("<html></html>", encoding="utf-8")
    assert incremental_update.detect_html(fic) == "fic"
    assert incremental_update.detect_html(ao3) == "ao3"
    assert incremental_update.detect_html(other) == "ao3"
