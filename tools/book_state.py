"""Series root, book sub-work and glossary master operations (pipeline v2, ticket 01).

A series root holds series metadata, a series glossary master and a series-level
story bible. Each book is a sub-work under ``books/book-NN`` and can be translated
independently. Legacy flat single-book works are left untouched.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

SERIES_FILE = "series.json"
SERIES_GLOSSARY = "glossary.json"
SERIES_BIBLE = "story_bible.md"
BOOKS_DIR = "books"
BOOK_PREFIX = "book-"

KIND_SERIES = "series"
KIND_LEGACY = "legacy"
KIND_UNKNOWN = "unknown"


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "untitled"


def series_dir(base: Path, series_id: str, title: str) -> Path:
    """Derive the conventional series root path: <base>/<series-id>-<slug>."""
    return base / f"{series_id}-{slugify(title)}"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def empty_glossary() -> dict:
    return {
        "version": 2,
        "high_frequency_top_n": 20,
        "applied_meta_hashes": {},
        "terms": [],
    }


def _master_path(root: Path) -> Path:
    return root / SERIES_GLOSSARY


def _book_glossary_path(book: Path) -> Path:
    return book / "work_temp" / SERIES_GLOSSARY


def _load_master(root: Path) -> dict:
    path = _master_path(root)
    return _read_json(path) if path.is_file() else empty_glossary()


def is_series_root(path: Path) -> bool:
    return (path / SERIES_FILE).is_file()


def work_kind(path: Path) -> str:
    if is_series_root(path):
        return KIND_SERIES
    if (path / "meta.json").is_file():
        return KIND_LEGACY
    return KIND_UNKNOWN


def load_series(root: Path) -> dict:
    path = root / SERIES_FILE
    if not path.is_file():
        raise FileNotFoundError(f"not a series root: {root}")
    data = _read_json(path)
    if not isinstance(data, dict):
        raise ValueError(f"invalid series metadata: {path}")
    return data


def save_series(root: Path, data: dict) -> Path:
    path = root / SERIES_FILE
    _write_json(path, data)
    return path


def book_slug(number: int) -> str:
    return f"{BOOK_PREFIX}{number:02d}"


def book_dir(root: Path, number: int) -> Path:
    return root / BOOKS_DIR / book_slug(number)


def resolve_book(root: Path, number: int) -> Path:
    series = load_series(root)
    if not any(entry.get("number") == number for entry in series.get("books") or []):
        raise FileNotFoundError(f"book {number} is not listed in {root / SERIES_FILE}")
    path = book_dir(root, number)
    if not path.is_dir():
        raise FileNotFoundError(f"book {number} not found under {root}")
    return path


def create_series(
    root: Path,
    *,
    series_id: str,
    title: str,
    author: str,
    source_url: str = "",
    books: list[dict] | None = None,
) -> Path:
    if (root / SERIES_FILE).exists():
        raise FileExistsError(f"series already exists: {root}")
    root.mkdir(parents=True, exist_ok=True)
    _write_json(_master_path(root), empty_glossary())
    (root / SERIES_BIBLE).write_text("# Series story bible\n", encoding="utf-8")
    save_series(
        root,
        {
            "series_id": series_id,
            "title": title,
            "author": author,
            "source_url": source_url,
            "status": "in-progress",
            "created_at": _now(),
            "books": [],
        },
    )
    for spec in books or []:
        create_book(root, int(spec["number"]), title=str(spec.get("title") or ""))
    return root


def _upsert_book_entry(series: dict, number: int, title: str) -> None:
    books = series.setdefault("books", [])
    entry = {
        "number": number,
        "slug": book_slug(number),
        "title": title,
        "status": "pending",
    }
    for i, existing in enumerate(books):
        if existing.get("number") == number:
            existing.update({"slug": entry["slug"], "title": title})
            books[i] = existing
            break
    else:
        books.append(entry)
    books.sort(key=lambda b: b.get("number", 0))


def create_book(root: Path, number: int, title: str = "") -> Path:
    series = load_series(root)
    path = book_dir(root, number)
    path.mkdir(parents=True, exist_ok=True)
    for name in ("source", "work_temp", "translation"):
        (path / name).mkdir(exist_ok=True)

    resolved_title = title or f"{series.get('title', '')} Book {number}"
    meta_path = path / "meta.json"
    if not meta_path.is_file():
        _write_json(
            meta_path,
            {
                "work_id": f"{series.get('series_id', 'series')}-{book_slug(number)}",
                "series_id": series.get("series_id"),
                "book_number": number,
                "title": resolved_title,
                "author": series.get("author", ""),
                "url": series.get("source_url", ""),
                "language": "English",
                "chapters": [],
                "chapter_count": 0,
                "word_count": 0,
                "source_files": [],
            },
        )
    _upsert_book_entry(series, number, resolved_title)
    save_series(root, series)
    if not _book_glossary_path(path).is_file():
        copy_glossary_to_book(root, number)
    return path


def merge_master_into_book(book_terms: list[dict], master_terms: list[dict]) -> list[dict]:
    """Add master terms that the book copy lacks; keep existing book terms."""
    merged = [dict(term) for term in book_terms]
    index = {_term_key(term): i for i, term in enumerate(merged)}
    for term in master_terms:
        key = _term_key(term)
        if key and key not in index:
            merged.append(dict(term))
            index[key] = len(merged) - 1
    return merged


def copy_glossary_to_book(root: Path, number: int) -> Path:
    book = resolve_book(root, number)
    master = _load_master(root)
    dest = _book_glossary_path(book)
    if dest.is_file():
        book_glossary = _read_json(dest)
        book_glossary["terms"] = merge_master_into_book(
            list(book_glossary.get("terms") or []), list(master.get("terms") or [])
        )
        _write_json(dest, book_glossary)
    else:
        _write_json(dest, master)
    return dest


def _term_key(term: dict) -> str:
    return str(term.get("id") or term.get("source") or "")


def merge_terms(master_terms: list[dict], book_terms: list[dict]) -> tuple[list[dict], dict]:
    """Merge book terms into the series master.

    The master stays authoritative for existing targets: a book-level target that
    disagrees is recorded as a conflict, not applied. New terms are added; additive
    fields (aliases, empty notes/metadata) are filled in.
    """
    merged = [dict(term) for term in master_terms]
    index = {_term_key(term): i for i, term in enumerate(merged)}
    added = updated = 0
    conflicts: list[dict] = []
    for term in book_terms:
        key = _term_key(term)
        if not key:
            continue
        if key not in index:
            merged.append(dict(term))
            index[key] = len(merged) - 1
            added += 1
            continue
        target = merged[index[key]]
        changed = False
        book_target = term.get("target") or ""
        master_target = target.get("target") or ""
        if book_target:
            if not master_target:
                target["target"] = book_target
                changed = True
            elif book_target != master_target:
                conflicts.append({"id": key, "master": master_target, "book": book_target})
        aliases = list(target.get("aliases") or [])
        for alias in term.get("aliases") or []:
            if alias not in aliases:
                aliases.append(alias)
                changed = True
        target["aliases"] = aliases
        if term.get("notes") and not target.get("notes"):
            target["notes"] = term["notes"]
            changed = True
        for field in ("category", "gender", "confidence"):
            if term.get(field) and not target.get(field):
                target[field] = term[field]
                changed = True
        if term.get("evidence_refs") and not target.get("evidence_refs"):
            target["evidence_refs"] = term["evidence_refs"]
            changed = True
        if changed:
            updated += 1
    return merged, {"added": added, "updated": updated, "conflicts": conflicts}


def merge_glossary_back(root: Path, number: int) -> dict:
    master = _load_master(root)
    book = resolve_book(root, number)
    book_path = _book_glossary_path(book)
    if not book_path.is_file():
        raise FileNotFoundError(f"book glossary not found: {book_path}")
    book_glossary = _read_json(book_path)
    terms, counts = merge_terms(
        list(master.get("terms") or []), list(book_glossary.get("terms") or [])
    )
    master["terms"] = terms
    _write_json(_master_path(root), master)
    return counts
