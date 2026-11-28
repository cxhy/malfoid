"""EPUB converter tests using synthetic, offline EPUB fixtures."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import epub_convert


def _epub(path: Path) -> Path:
    package = """<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>Test Book</dc:title>
    <dc:creator>Test Author</dc:creator>
    <dc:source>https://example.test/story/1</dc:source>
    <dc:description>A test book.</dc:description>
    <meta property="belongs-to-collection">Test Saga</meta>
    <meta property="malfoid:status">complete</meta>
  </metadata>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="cover" href="cover.xhtml" media-type="application/xhtml+xml"/>
    <item id="c1" href="chapter-001.xhtml" media-type="application/xhtml+xml"/>
    <item id="c2" href="chapter-002.xhtml" media-type="application/xhtml+xml"/>
    <item id="i1" href="interlude-the-thing.xhtml" media-type="application/xhtml+xml"/>
    <item id="c3" href="chapter-003.xhtml" media-type="application/xhtml+xml"/>
    <item id="sources" href="sources.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine>
    <itemref idref="cover"/>
    <itemref idref="c1"/>
    <itemref idref="c2"/>
    <itemref idref="i1"/>
    <itemref idref="c3"/>
    <itemref idref="sources"/>
  </spine>
</package>
"""
    nav = """<html><body>
<nav epub:type="toc"><ol>
  <li><a href="chapter-001.xhtml">Chapter 1: First</a></li>
  <li><a href="chapter-002.xhtml">Chapter 2: Second</a></li>
  <li><a href="interlude-the-thing.xhtml">Interlude: The Thing</a></li>
  <li><a href="chapter-003.xhtml">Chapter 3: Third</a></li>
  <li><a href="sources.xhtml">About this edition</a></li>
</ol></nav>
<nav epub:type="landmarks"><ol>
  <li><a href="chapter-001.xhtml">Start of the book</a></li>
</ol></nav>
</body></html>"""
    chapter1 = """<html><body><section epub:type="chapter">
<p class="chapter-num">Chapter 1</p><h1>First</h1>
<div class="prose"><p>One <em>emphasised</em> paragraph.</p><p>Second paragraph.</p></div>
</section></body></html>"""
    chapter2 = """<html><body><section epub:type="chapter">
<p class="chapter-num">Chapter 2</p><h1>Second</h1>
<div class="prose"><p>Another paragraph.</p></div>
</section></body></html>"""
    interlude = """<html><body><section epub:type="chapter">
<p class="chapter-num">Interlude</p><h1>The Thing</h1>
<div class="prose"><p>An interlude paragraph.</p></div>
</section></body></html>"""
    chapter3 = """<html><body><section epub:type="chapter">
<p class="chapter-num">Chapter 3</p><h1>Third</h1>
<div class="prose"><p>Final paragraph.</p></div>
</section></body></html>"""
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr(
            "META-INF/container.xml",
            '<?xml version="1.0"?><container><rootfiles>'
            '<rootfile full-path="OEBPS/package.opf" media-type="application/oebps-package+xml"/>'
            "</rootfiles></container>",
        )
        archive.writestr("OEBPS/package.opf", package)
        archive.writestr("OEBPS/nav.xhtml", nav)
        archive.writestr("OEBPS/cover.xhtml", "<html><body><p>Cover</p></body></html>")
        archive.writestr("OEBPS/chapter-001.xhtml", chapter1)
        archive.writestr("OEBPS/chapter-002.xhtml", chapter2)
        archive.writestr("OEBPS/interlude-the-thing.xhtml", interlude)
        archive.writestr("OEBPS/chapter-003.xhtml", chapter3)
        archive.writestr(
            "OEBPS/sources.xhtml", "<html><body><p>About this edition.</p></body></html>"
        )
    return path


def test_read_epub_keeps_spine_order_numbers_and_interlude(tmp_path: Path) -> None:
    data = epub_convert.read_epub(_epub(tmp_path / "book.epub"))
    assert data["title"] == "Test Book"
    assert data["author"] == "Test Author"
    assert data["series"] == "Test Saga"
    assert data["status"] == "complete"
    assert [chapter["index"] for chapter in data["chapters"]] == [1, 2, 2.5, 3]
    assert [chapter["marker"] for chapter in data["chapters"]] == [
        "Ch 1: First",
        "Ch 2: Second",
        "Ch 2.5: Interlude: The Thing",
        "Ch 3: Third",
    ]
    assert data["chapters"][0]["paragraphs"] == [
        "One*emphasised*paragraph.",
        "Second paragraph.",
    ]


def test_convert_writes_work_meta_and_original(tmp_path: Path) -> None:
    epub = _epub(tmp_path / "book.epub")
    output = tmp_path / "out"
    epub_convert.convert(
        epub,
        output,
        work_id="test-book",
        title="Test Book",
        author="Test Author",
        series="Test Saga",
        source_url="https://example.test/story/1",
    )
    work = (output / "work.md").read_text(encoding="utf-8")
    assert work.startswith("Ch 1: First\n\nOne*emphasised*paragraph.")
    assert "Ch 2.5: Interlude: The Thing" in work
    assert "About this edition" not in work
    meta = json.loads((output / "meta.json").read_text(encoding="utf-8"))
    assert meta["chapter_count"] == 4
    assert meta["source_complete"] is True
    assert meta["source_sha256"]
    assert (output / "original" / "book.epub").is_file()
