"""Converter tests: AO3 / FicHub HTML and PDF text parsing.

These tests use small synthetic fixtures and never touch the network.
"""

from __future__ import annotations

import ao3_convert
import fic_convert
import pdf_convert
from bs4 import BeautifulSoup


def test_ao3_collapse_and_slugify() -> None:
    assert ao3_convert.collapse("a\n\n  b\t c") == "a b c"
    assert ao3_convert.slugify("The Girl Who Loved") == "the-girl-who-loved"
    assert ao3_convert.slugify("!!!") == "untitled"


AO3_FIXTURE = """
<html><body>
<div id="preface">
  <h2 class="title">A Test Work</h2>
  <dl class="work meta group">
    <dt>Rating:</dt><dd>General Audiences</dd>
    <dt>Language:</dt><dd>English</dd>
  </dl>
</div>
<div id="chapters">
  <div class="meta">
    <h1>Chapter 1: Start</h1>
  </div>
  <div class="userstuff">
    <p>First paragraph.</p>
    <p>Second <em>paragraph</em>.</p>
  </div>
  <div class="meta">
    <h1>Chapter 2: End</h1>
  </div>
  <div class="userstuff">
    <p>Final paragraph.</p>
  </div>
</div>
</body></html>
"""


def test_ao3_find_chapters() -> None:
    soup = BeautifulSoup(AO3_FIXTURE, "html.parser")
    chapters = ao3_convert.find_chapters(soup)
    assert [c["title"] for c in chapters] == ["Chapter 1: Start", "Chapter 2: End"]
    assert "First paragraph." in chapters[0]["body"]
    assert "*paragraph*" in chapters[0]["body"]
    assert "Final paragraph." in chapters[1]["body"]


FIC_FIXTURE = """
<html><body>
<div id="chap_1">
  <h2>Chapter 1: A Title</h2>
  <div class="chapter_nav"><a href="#">Next</a></div>
  <p>Body one.</p>
  <p>Body two.</p>
</div>
</body></html>
"""


def test_fic_chapter_text() -> None:
    soup = BeautifulSoup(FIC_FIXTURE, "html.parser")
    heading, body = fic_convert.chapter_text(soup.find(id="chap_1"))
    assert heading == "Chapter 1: A Title"
    assert "Body one." in body and "Body two." in body
    assert "Next" not in body


PDF_FIXTURE = """\
1.\u200b Chapter One
     The first paragraph starts here and
wraps onto a second line.

     A second paragraph.

2.\u200b Chapter Two
     Another body.
"""


def test_pdf_parse_chapters() -> None:
    chapters = pdf_convert.parse_chapters(PDF_FIXTURE)
    assert [c["index"] for c in chapters] == [1, 2]
    assert chapters[0]["title"] == "Chapter One"
    assert len(chapters[0]["paragraphs"]) == 2
    assert "wraps onto a second line" in chapters[0]["paragraphs"][0]
    assert chapters[1]["paragraphs"] == ["Another body."]


def test_pdf_parse_chapters_requires_sequential_numbers() -> None:
    text = "1.\u200b First\n     body\n\n3.\u200b Skipped\n     body\n"
    chapters = pdf_convert.parse_chapters(text)
    assert [c["index"] for c in chapters] == [1]
