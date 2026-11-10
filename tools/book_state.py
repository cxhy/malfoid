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
GLOBAL_GLOSSARY_DIR = "glossary"
GLOBAL_GLOSSARY_FILE = "hp.json"
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


def global_glossary_path(root: Path) -> Path:
    """Return the workspace global glossary path for a series root.

    The global table is intentionally outside ``works/`` so it can be reviewed
    and versioned carefully. It holds canonical HP terms plus the project-wide
    gender-swapped Malfoy rule, never series-specific or AU terms.
    """
    return root.parent.parent / GLOBAL_GLOSSARY_DIR / GLOBAL_GLOSSARY_FILE


def load_global_terms(root: Path) -> list[dict]:
    """Load canonical global terms; missing or invalid file means no global layer."""
    path = global_glossary_path(root)
    if not path.is_file():
        return []
    try:
        data = _read_json(path)
    except (OSError, ValueError):
        return []
    return [dict(term) for term in data.get("terms") or [] if isinstance(term, dict)]


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
    sync_glossary(path)
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
    master_terms, _ = merge_global_series(
        load_global_terms(root), list(master.get("terms") or [])
    )
    dest = _book_glossary_path(book)
    if dest.is_file():
        book_glossary = _read_json(dest)
        book_glossary["terms"] = merge_master_into_book(
            list(book_glossary.get("terms") or []), master_terms
        )
        _write_json(dest, book_glossary)
    else:
        data = dict(master)
        data["terms"] = master_terms
        _write_json(dest, data)
    return dest


def _term_key(term: dict) -> str:
    return str(term.get("id") or term.get("source") or "")


def _merge_additive_fields(target: dict, term: dict) -> bool:
    """Merge aliases, notes, metadata and forbidden variants into a term."""
    changed = False
    aliases = list(target.get("aliases") or [])
    for alias in term.get("aliases") or []:
        if alias not in aliases:
            aliases.append(alias)
            changed = True
    if aliases:
        target["aliases"] = aliases
    for field in ("notes", "category", "gender", "confidence"):
        if term.get(field) and not target.get(field):
            target[field] = term[field]
            changed = True
    for field in ("forbidden_targets", "evidence_refs"):
        existing = list(target.get(field) or [])
        for value in term.get(field) or []:
            if value not in existing:
                existing.append(value)
                changed = True
        if existing:
            target[field] = existing
    return changed


def _has_extra_fields(term: dict, base: dict) -> bool:
    """True when a book term adds fields beyond the canonical global term."""
    for field in ("aliases", "forbidden_targets", "evidence_refs"):
        base_values = {str(value) for value in base.get(field) or []}
        for value in term.get(field) or []:
            if str(value) not in base_values:
                return True
    base_notes = str(base.get("notes") or "")
    term_notes = str(term.get("notes") or "")
    return bool(term_notes and term_notes != base_notes)


def merge_global_series(
    global_terms: list[dict], series_terms: list[dict]
) -> tuple[list[dict], list[dict]]:
    """Merge a series master over the global baseline.

    The global table is authoritative for canonical terms. A series term with a
    different target must set ``override_global: true``; otherwise the global
    target wins and the disagreement is returned as a conflict. Series-only terms
    are added as-is, and additive fields (aliases, notes, forbidden variants) are
    always merged.
    """
    merged = [dict(term) for term in global_terms]
    index = {_term_key(term): position for position, term in enumerate(merged)}
    conflicts: list[dict] = []
    for term in series_terms:
        key = _term_key(term)
        if not key:
            continue
        if key not in index:
            merged.append(dict(term))
            index[key] = len(merged) - 1
            continue
        target = merged[index[key]]
        override = bool(term.get("override_global"))
        series_target = term.get("target") or ""
        global_target = target.get("target") or ""
        if series_target and global_target and series_target != global_target:
            if override:
                target["target"] = series_target
            else:
                conflicts.append(
                    {"id": key, "global": global_target, "series": series_target}
                )
        if override:
            for field in ("source", "target"):
                if term.get(field):
                    target[field] = term[field]
        _merge_additive_fields(target, term)
    return merged, conflicts


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
        if _merge_additive_fields(target, term):
            changed = True
        if changed:
            updated += 1
    return merged, {"added": added, "updated": updated, "conflicts": conflicts}


def merged_glossary_terms(book: Path) -> list[dict]:
    """Return global + series + book terms for active use.

    Resolution order: global canonical baseline, then the series master, then
    the book copy. The series master can override a global target only with
    ``override_global: true``; otherwise the global target wins and the series
    disagreement is a reported conflict. Book terms supplement the series: they
    cannot replace an existing target, but their additive fields are merged.
    """
    book = Path(book)
    root = book.parent.parent
    global_terms = load_global_terms(root)
    series_terms = list(_load_master(root).get("terms") or []) if is_series_root(root) else []
    merged, _ = merge_global_series(global_terms, series_terms)
    index = {_term_key(term): i for i, term in enumerate(merged)}
    book_path = _book_glossary_path(book)
    book_terms = []
    if book_path.is_file():
        book_terms = list(_read_json(book_path).get("terms") or [])
    for term in book_terms:
        key = _term_key(term)
        if not key:
            continue
        if key not in index:
            merged.append(dict(term))
            index[key] = len(merged) - 1
            continue
        target = merged[index[key]]
        for field in ("source", "target"):
            if not target.get(field) and term.get(field):
                target[field] = term[field]
        _merge_additive_fields(target, term)
    return merged


def sync_glossary(book: Path) -> Path:
    """Refresh a book glossary from the series master and return its path."""
    book = Path(book)
    path = _book_glossary_path(book)
    data = _read_json(path) if path.is_file() else empty_glossary()
    data["terms"] = merged_glossary_terms(book)
    _write_json(path, data)
    return path


def load_glossary_terms(book: Path) -> list[dict]:
    """Load and synchronize the merged glossary for translation/review use."""
    sync_glossary(book)
    return merged_glossary_terms(book)


def _parse_story_bible_terms(text: str) -> list[dict]:
    """Extract unambiguous English→Chinese rows from story-bible tables."""
    terms: list[dict] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if len(cells) < 2:
            continue
        source = cells[0].strip("*` ")
        target = cells[1].strip("*` ")
        if not source or not target:
            continue
        if not re.search(r"[A-Za-z]", source):
            continue
        if "/" in source or "/" in target:
            continue
        if any(marker in target for marker in ("或", "保留", "视上下文", "按语境", "待定")):
            continue
        terms.append(
            {
                "id": source,
                "source": source,
                "target": target,
                "aliases": [],
                "category": "term",
                "gender": "unknown",
                "confidence": "medium",
                "frequency": 0,
                "evidence_refs": [],
                "notes": "story bible term",
            }
        )
    return terms


def load_story_bible_terms(book: Path) -> list[dict]:
    """Load supplementary terms from the book and series story bibles."""
    book = Path(book)
    paths = [book / "work_temp" / "prescan" / "story_bible.md"]
    root = book.parent.parent
    if is_series_root(root):
        paths.append(root / SERIES_BIBLE)
    seen: set[str] = set()
    terms: list[dict] = []
    for path in paths:
        if not path.is_file():
            continue
        for term in _parse_story_bible_terms(path.read_text(encoding="utf-8")):
            key = term["source"].casefold()
            if key in seen:
                continue
            seen.add(key)
            terms.append(term)
    return terms


def load_context_terms(book: Path) -> list[dict]:
    """Merge glossary and story-bible terms; the glossary always wins."""
    terms = load_glossary_terms(book)
    seen = set()
    for term in terms:
        source = str(term.get("source") or term.get("id") or "")
        if source:
            seen.add(source.casefold())
        for alias in term.get("aliases") or []:
            if isinstance(alias, str) and alias:
                seen.add(alias.casefold())
    for term in load_story_bible_terms(book):
        key = term["source"].casefold()
        if key in seen:
            continue
        seen.add(key)
        terms.append(term)
    return terms


def merge_glossary_back(root: Path, number: int) -> dict:
    """Merge book-local terms into the series master, not the global table.

    Global terms that the book copy carries are ignored unless the book adds
    additive fields; a book-level target that disagrees with a global canonical
    term is a conflict and is not written. Series-only book terms are added, and
    existing series targets stay authoritative. The global table is never edited
    by this operation.
    """
    master = _load_master(root)
    global_terms = load_global_terms(root)
    global_index = {_term_key(term): i for i, term in enumerate(global_terms)}
    book = resolve_book(root, number)
    book_path = _book_glossary_path(book)
    if not book_path.is_file():
        raise FileNotFoundError(f"book glossary not found: {book_path}")
    book_glossary = _read_json(book_path)
    merged = [dict(term) for term in master.get("terms") or []]
    series_index = {_term_key(term): i for i, term in enumerate(merged)}
    added = updated = 0
    conflicts: list[dict] = []
    for term in book_glossary.get("terms") or []:
        key = _term_key(term)
        if not key:
            continue
        if key in series_index:
            target = merged[series_index[key]]
            changed = False
            book_target = term.get("target") or ""
            series_target = target.get("target") or ""
            if book_target:
                if not series_target:
                    target["target"] = book_target
                    changed = True
                elif book_target != series_target:
                    conflicts.append(
                        {"id": key, "master": series_target, "book": book_target}
                    )
            if not target.get("source") and term.get("source"):
                target["source"] = term["source"]
                changed = True
            if _merge_additive_fields(target, term):
                changed = True
            if changed:
                updated += 1
            continue
        if key in global_index:
            global_term = global_terms[global_index[key]]
            global_target = global_term.get("target") or ""
            book_target = term.get("target") or ""
            if book_target and global_target and book_target != global_target:
                conflicts.append(
                    {"id": key, "global": global_target, "book": book_target}
                )
                continue
            if _has_extra_fields(term, global_term):
                new_term = dict(term)
                new_term["source"] = global_term.get("source") or term.get("source") or key
                new_term["target"] = global_target or term.get("target") or ""
                new_term.setdefault("override_global", False)
                merged.append(new_term)
                series_index[key] = len(merged) - 1
                added += 1
            continue
        merged.append(dict(term))
        series_index[key] = len(merged) - 1
        added += 1
    master["terms"] = merged
    _write_json(_master_path(root), master)
    return {"added": added, "updated": updated, "conflicts": conflicts}
