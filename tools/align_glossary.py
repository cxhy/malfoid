#!/usr/bin/env python3
"""Deterministic glossary coverage and canonical-target alignment.

Usage:
    .venv/bin/python tools/align_glossary.py works/<series>/books/book-01
    .venv/bin/python tools/align_glossary.py <book> --strict-coverage
    .venv/bin/python tools/align_glossary.py <book> --discover
    .venv/bin/python tools/align_glossary.py <book> --apply-forbidden --dry-run

The tool never calls a model. It treats the merged glossary as authoritative:
source forms and aliases select entries; canonical targets and their components
are accepted in the translation; forbidden targets are errors. It also audits
source proper nouns that are not covered by the glossary, and can suggest
candidate variants for human review.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import book_state
import profile_rules
import segment_state

PROPER_NOUN_RE = re.compile(
    r"\b(?:The\s+)?[A-Z][A-Za-z'’\-]*(?:\s+[A-Z][A-Za-z'’\-]*)*\b"
)
ACRONYM_RE = re.compile(r"\b[A-Z]{2,}\b")
LOWERCASE_TOKEN_RE = re.compile(r"[a-z][A-Za-z'’\-]*")
CHINESE_RUN_RE = re.compile(r"[\u4e00-\u9fff]{2,}")
TARGET_SEPARATORS = ("·", "-", "—", "–", "/", "、")
TARGET_SUFFIXES = (
    "一家", "家族", "小姐", "先生", "夫人", "女士", "教授", "医院", "学校",
    "学院", "城堡", "庄园", "商店", "球队", "课程", "咒语", "魔咒", "药水",
    "列车", "站台", "街", "路", "村", "塔", "楼", "房间", "部门", "科室", "队",
)

ENGLISH_STOPWORDS = frozenset(
    """
    a an the and or but if because although while since until unless though yet
    so then now later soon finally suddenly quickly slowly maybe perhaps probably
    certainly actually really very too also however therefore instead meanwhile
    otherwise besides moreover furthermore nevertheless nonetheless consequently
    accordingly similarly likewise indeed still even just only not no yes oh ah
    well i you he she it we they me him her us them my your his its our their
    this that these those what who whom whose which when where why how
    as at after before during for from in into of off on onto out over past per
    than through to toward towards under up upon with within without by
    about above across against along among around behind below beneath beside
    between beyond down near next inside outside
    mr mrs ms miss dr prof professor sir madam lady lord headmaster headmistress
    chapter part book volume prologue epilogue interlude appendix
    january february march april may june july august september october november december
    monday tuesday wednesday thursday friday saturday sunday
    christmas easter halloween valentine birthday
    minister ministry master mistress madame monsieur mademoiselle
    miss mister sir lady lord headmaster headmistress
    american british french german italian spanish irish scottish welsh
    european asian african
    mmm hm hmm yep yup nope agh augh ugh ah oh jus
    regardless plus precisely positive congratulations goodnight pardon bye hi whoa nah oi
    ew ooooh aaah aye blimey c'mon pfft sssh eurgh goood alrigh cann'a on'y tis wha whats wh
    w-what i-i h-he j-just o-oh p-potter poo-ter nee iz eet harrumphmint goin bumped lassie
    l ii st ladies humans lots missile regulation sod odder harrumphmint don
    bulgarian bulgarians albanian
    """.split()
)
TITLE_WORDS = frozenset(
    "lord lady miss mister mrs mr ms madame madam professor prof master mistress "
    "uncle aunt sir dr doctor headmaster headmistress minister".split()
)
LEADING_NOISE = frozenset(
    """
    as when but if while after before to at for in on with without neither
    regardless plus sorry oh hi bye goodnight congratulations pardon whoa nah oi
    support merry
    """.split()
)
PARTIAL_ENDINGS = frozenset(
    "against of in on with and or for to from by at as the".split()
)

CHINESE_PARTICLES = frozenset("的了在是我你他她它们这那有不就都也还又很太更最会能要把被从到和与及或但而却才只再没吗呢吧啊呀哦嗯两")
CHINESE_STOPWORDS = frozenset(
    """
    一个 一些 一样 一起 一边 一切 一直 一般 一切 一下 一切 这个 那个 这些 那些
    什么 怎么 怎样 为什么 哪里 这里 那里 自己 大家 别人 有人 没人 人们
    但是 可是 然而 不过 因为 所以 然后 于是 而且 并且 或者 还是 就是 不是 只是
    已经 曾经 正在 将要 可以 能够 应该 必须 需要 没有 不是 不会 不能 不要
    知道 觉得 认为 看到 看着 听见 听到 说道 说着 问道 问着 叫道 喊着 低声 大声
    时候 时间 现在 当时 之后 之前 以后 以前 今天 昨天 明天 晚上 早上 下午
    眼睛 脸上 心里 手里 身上 面前 身后 旁边 周围 房间 地方 东西 事情 问题
    声音 语气 表情 目光 眼神 嘴角 头发 身体 手臂 手指 脚下 地上 桌上
    似乎 突然 终于 立刻 马上 慢慢 轻轻 静静 悄悄 默默 缓缓 微微 深深
    一样 一切 一边 一起 一直 一点 一些 一切 这种 那种 这样 那样 这时 那时
    学校 教室 走廊 城堡 魔法 咒语 魔杖 学院 学生 老师 教授 朋友 同学
    发生 成为 开始 结束 继续 出现 离开 回来 进去 出来 过去 起来
    男孩 女孩 男生 女生 孩子 少年 少女 男人 女人 父亲 母亲 妈妈 爸爸
    先生 小姐 夫人 女士 博士 校长 级长 主席
    """.split()
)


@dataclass(frozen=True)
class AlignmentTerm:
    """One glossary entry reduced to forms useful for deterministic checks."""

    term_id: str
    sources: tuple[str, ...]
    target: str
    canonical_forms: tuple[str, ...]
    forbidden: tuple[str, ...]
    category: str
    forbidden_source_scoped: bool
    source_patterns: tuple[re.Pattern[str], ...]


def _chapters(book_dir: Path) -> list[dict[str, Any]]:
    chapters_dir = Path(book_dir) / "source" / segment_state.CHAPTERS_DIR

    def sort_key(path: Path) -> float:
        match = re.search(r"(\d+(?:\.\d+)?)$", path.stem)
        return float(match.group(1)) if match else 0.0

    return [
        segment_state.load_chapter(path)
        for path in sorted(chapters_dir.glob("ch-*.json"), key=sort_key)
    ]


def _target_forms(target: str) -> tuple[str, ...]:
    """Return the canonical target and its unambiguous components.

    ``阿莉莎·特威格斯`` yields the full form and ``阿莉莎``/``特威格斯`` so a
    translation may use a first name or surname alone. Ambiguous alternatives
    (``甲/乙``, ``甲或乙``) are skipped: only forbidden variants are checked.
    """
    text = target.strip()
    if not text or "/" in text or "或" in text or "|" in text:
        return ()
    forms = [text]
    components: list[str] = []
    for part in re.split(r"[·\-—–\s、]+", text):
        cleaned = part.strip("（）()[]【】,，。.;；:：")
        if len(cleaned) >= 2 and cleaned != text:
            components.append(cleaned)
    for component in components:
        if component not in forms:
            forms.append(component)
    for suffix in TARGET_SUFFIXES:
        if text.endswith(suffix) and len(text) - len(suffix) >= 2:
            prefix = text[: -len(suffix)]
            if prefix not in forms:
                forms.append(prefix)
    return tuple(forms)


def _source_pattern(source: str) -> re.Pattern[str] | None:
    """Compile a case-insensitive source/alias matcher with token boundaries."""
    text = source.strip()
    if len(text) < 2:
        return None
    if re.search(r"[A-Za-z]", text):
        pieces = [piece for piece in re.split(r"\s+", text) if piece]
        body = r"\s+".join(re.escape(piece) for piece in pieces)
        pattern = r"(?<![A-Za-z0-9])" + body + r"(?![A-Za-z0-9])"
        return re.compile(pattern, re.IGNORECASE)
    return re.compile(re.escape(text))


def build_alignment_terms(terms: list[Any] | None) -> list[AlignmentTerm]:
    """Build the canonical index from glossary entries."""
    result: list[AlignmentTerm] = []
    for term in terms or []:
        if not isinstance(term, dict):
            continue
        term_id = str(term.get("id") or term.get("source") or "").strip()
        source = str(term.get("source") or term_id).strip()
        target = str(term.get("target") or "").strip()
        aliases = [
            str(alias).strip()
            for alias in term.get("aliases") or []
            if isinstance(alias, str) and str(alias).strip()
        ]
        sources: list[str] = []
        seen: set[str] = set()
        for value in [source, *aliases]:
            if not value:
                continue
            key = value.casefold()
            if key in seen:
                continue
            seen.add(key)
            sources.append(value)
        if not sources:
            continue
        forbidden = tuple(
            str(value).strip()
            for value in term.get("forbidden_targets") or []
            if isinstance(value, str) and str(value).strip()
        )
        patterns = tuple(
            pattern
            for pattern in (_source_pattern(value) for value in sources)
            if pattern is not None
        )
        if not patterns:
            continue
        forms = list(_target_forms(target))
        for value in term.get("canonical_forms") or []:
            if isinstance(value, str) and value.strip():
                form = value.strip()
                if form not in forms:
                    forms.append(form)
        result.append(
            AlignmentTerm(
                term_id=term_id,
                sources=tuple(sources),
                target=target,
                canonical_forms=tuple(forms),
                forbidden=forbidden,
                category=str(term.get("category") or "term"),
                forbidden_source_scoped=bool(term.get("forbidden_source_scoped")),
                source_patterns=patterns,
            )
        )
    return result


def protected_canonical_forms(index: list[AlignmentTerm]) -> set[str]:
    """Return every canonical form; forbidden variants inside them are legal."""
    forms: set[str] = set()
    for term in index:
        forms.update(term.canonical_forms)
    return forms


def _term_matches(term: AlignmentTerm, source: str) -> bool:
    return any(pattern.search(source) for pattern in term.source_patterns)


def _contains(text: str, form: str) -> bool:
    if not form:
        return False
    if re.search(r"[A-Za-z]", form):
        return form.casefold() in text.casefold()
    return form in text


def _first_variant(
    term: AlignmentTerm, target: str, protected_forms: set[str] | None = None
) -> str | None:
    protected = protected_forms or set()
    for variant in term.forbidden:
        if not variant or variant in term.canonical_forms:
            continue
        if not _contains(target, variant):
            continue
        # A forbidden substring is legal only when the target actually contains
        # the canonical form that owns that substring.
        if any(variant in form and _contains(target, form) for form in protected):
            continue
        return variant
    return None


def _segment_location(chapter: dict[str, Any], segment: dict[str, Any]) -> dict[str, Any]:
    return {
        "chapter": chapter.get("chapter"),
        "index": segment.get("index"),
        "source": (segment.get("source") or "")[:160],
        "target": (segment.get("target") or "")[:160],
    }


def scan_alignment(
    book_dir: Path,
    terms: list[Any] | None = None,
    chapters: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Scan translated segments for forbidden variants and missing canonical forms.

    Forbidden variants are errors. Missing canonical forms are warnings: a
    legitimate translation may replace a repeated name with a pronoun.
    """
    book = Path(book_dir)
    glossary = terms if terms is not None else book_state.load_glossary_terms(book)
    index = build_alignment_terms(glossary)
    protected = protected_canonical_forms(index)
    chapters = chapters if chapters is not None else _chapters(book)
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    for chapter in chapters:
        for segment in chapter.get("segments") or []:
            if segment.get("kind") == "heading":
                continue
            source = segment.get("source") or ""
            target = segment.get("target") or ""
            if not source or not target.strip():
                continue
            for term in index:
                location = _segment_location(chapter, segment)
                if term.forbidden_source_scoped and not _term_matches(term, source):
                    continue
                variant = _first_variant(term, target, protected)
                if variant:
                    errors.append(
                        {
                            "kind": "glossary_variant",
                            "term": term.term_id,
                            "variant": variant,
                            "category": term.category,
                            **location,
                        }
                    )
                    continue
                if not _term_matches(term, source):
                    continue
                if term.canonical_forms and not any(
                    _contains(target, form) for form in term.canonical_forms
                ):
                    warnings.append(
                        {
                            "kind": "canonical_missing",
                            "term": term.term_id,
                            "canonical": term.target,
                            "category": term.category,
                            **location,
                        }
                    )
    return {
        "errors": errors,
        "warnings": warnings,
        "counts": {
            "terms": len(index),
            "segments": sum(len(chapter.get("segments") or []) for chapter in chapters),
            "variant_errors": len(errors),
            "canonical_missing": len(warnings),
        },
    }


def _source_blob(chapters: list[dict[str, Any]]) -> str:
    return "\n".join(
        (segment.get("source") or "")
        for chapter in chapters
        for segment in chapter.get("segments") or []
        if segment.get("kind") != "heading"
    )


def _covered_sources(terms: list[Any] | None) -> set[str]:
    covered: set[str] = set()
    for term in terms or []:
        if not isinstance(term, dict):
            continue
        values = [term.get("source"), term.get("id"), *(term.get("aliases") or [])]
        for value in values:
            if not isinstance(value, str) or not value.strip():
                continue
            text = value.strip()
            covered.add(text.casefold())
            for word in text.split():
                if word:
                    covered.add(word.casefold())
    return covered


def _profile_sources(book_dir: Path) -> set[str]:
    female = profile_rules.female_draco(work_dir=book_dir)
    values = female.get("source_forms") if isinstance(female, dict) else []
    return {
        str(value).strip().casefold()
        for value in values or []
        if isinstance(value, str) and str(value).strip()
    }


def _strip_leading_noise(candidate: str) -> str:
    words = candidate.split()
    while words and words[0].casefold() in LEADING_NOISE:
        words.pop(0)
    return " ".join(words)


def _strip_titles(candidate: str) -> str:
    words = candidate.split()
    while len(words) > 1 and words[0].casefold() in TITLE_WORDS:
        words.pop(0)
    return " ".join(words)


def _all_words_known(
    words: list[str], covered: set[str], lowercase_counts: dict[str, int]
) -> bool:
    for word in words:
        key = word.casefold()
        if key in covered or key in ENGLISH_STOPWORDS or lowercase_counts.get(key, 0) >= 2:
            continue
        return False
    return True


def _plural_base(candidate: str) -> str:
    if candidate.endswith("s") and not candidate.endswith("ss") and len(candidate) > 2:
        return candidate[:-1]
    return ""


def _candidate_tokens(text: str) -> list[str]:
    tokens: list[str] = []
    for match in PROPER_NOUN_RE.finditer(text):
        candidate = match.group(0).strip()
        if candidate.startswith("The "):
            candidate = candidate[4:].strip()
        candidate = _strip_leading_noise(candidate)
        if candidate:
            tokens.append(candidate)
    for match in ACRONYM_RE.finditer(text):
        candidate = match.group(0)
        if candidate:
            tokens.append(candidate)
    return tokens


def _normalise_candidate(candidate: str) -> str:
    """Strip possessives and contractions so the base name can be checked."""
    text = candidate.strip()
    if not text:
        return ""
    if text.lower().endswith("n't"):
        return ""
    match = re.match(r"^(.*?)[’'](s|m|ll|ve|re|d|t)$", text, re.IGNORECASE)
    if match and match.group(1).strip():
        return match.group(1).strip()
    return text


def _coverage_ignores(book: Path) -> set[str]:
    """Load explicit coverage exceptions from the series glossary master."""
    series = book.parent.parent / book_state.SERIES_GLOSSARY
    if not series.is_file():
        return set()
    try:
        data = json.loads(series.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    return {
        str(value).casefold()
        for value in data.get("coverage_ignore") or []
        if str(value).strip()
    }


def _lowercase_counts(blob: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for match in LOWERCASE_TOKEN_RE.finditer(blob):
        key = match.group(0).casefold()
        counts[key] = counts.get(key, 0) + 1
    return counts


def _is_common_candidate(candidate: str, lowercase_counts: dict[str, int]) -> bool:
    words = candidate.split()
    if not words:
        return True
    if len(words) == 1:
        key = words[0].casefold()
        if key in ENGLISH_STOPWORDS or lowercase_counts.get(key, 0) >= 2:
            return True
        if key.endswith("ing") and len(key) > 4:
            return True
        return False
    if words[-1].casefold() in PARTIAL_ENDINGS:
        return True
    if all(word.isupper() for word in words):
        return True
    common = []
    for word in words:
        key = word.casefold()
        common.append(
            key in ENGLISH_STOPWORDS or lowercase_counts.get(key, 0) >= 2
        )
    return all(common)


def coverage_report(
    book_dir: Path,
    terms: list[Any] | None = None,
    chapters: list[dict[str, Any]] | None = None,
    min_count: int = 2,
) -> dict[str, Any]:
    """Audit source proper nouns that the glossary does not cover."""
    book = Path(book_dir)
    glossary = terms if terms is not None else book_state.load_glossary_terms(book)
    chapters = chapters if chapters is not None else _chapters(book)
    covered = _covered_sources(glossary) | _profile_sources(book)
    ignores = _coverage_ignores(book)
    blob = _source_blob(chapters)
    lowercase_counts = _lowercase_counts(blob)
    counts: dict[str, dict[str, Any]] = {}
    locations: dict[str, list[dict[str, Any]]] = {}
    for chapter in chapters:
        for segment in chapter.get("segments") or []:
            if segment.get("kind") == "heading":
                continue
            source = segment.get("source") or ""
            for raw in _candidate_tokens(source):
                candidate = _normalise_candidate(raw)
                if not candidate:
                    continue
                candidate = _strip_titles(candidate)
                if not candidate:
                    continue
                key = candidate.casefold()
                base = _plural_base(candidate)
                if key in covered or (base and base.casefold() in covered):
                    continue
                if key in ignores or (base and base.casefold() in ignores):
                    continue
                if candidate.endswith("-") or any(len(word) == 1 for word in candidate.split()):
                    continue
                if _is_common_candidate(candidate, lowercase_counts):
                    continue
                words = candidate.split()
                if len(words) > 1 and _all_words_known(words, covered, lowercase_counts):
                    continue
                entry = counts.setdefault(key, {"candidate": candidate, "count": 0})
                entry["count"] += 1
                if len(locations.setdefault(key, [])) < 3:
                    locations[key].append(
                        {"chapter": chapter.get("chapter"), "index": segment.get("index")}
                    )
    missing: list[dict[str, Any]] = []
    for key, entry in sorted(
        counts.items(), key=lambda item: (-item[1]["count"], item[1]["candidate"])
    ):
        count = entry["count"]
        if count < min_count:
            continue
        missing.append(
            {
                "candidate": entry["candidate"],
                "count": count,
                "locations": locations.get(key, []),
            }
        )
    return {
        "missing": missing,
        "counts": {
            "candidates": len(counts),
            "missing": len(missing),
            "min_count": min_count,
        },
    }


def _chinese_candidates(text: str) -> list[str]:
    candidates: list[str] = []
    for run in CHINESE_RUN_RE.findall(text):
        if len(run) < 2:
            continue
        for size in range(2, min(5, len(run)) + 1):
            for start in range(0, len(run) - size + 1):
                candidate = run[start : start + size]
                if candidate in CHINESE_STOPWORDS:
                    continue
                if any(character in CHINESE_PARTICLES for character in candidate):
                    continue
                candidates.append(candidate)
    return candidates


def discover_variants(
    book_dir: Path,
    terms: list[Any] | None = None,
    chapters: list[dict[str, Any]] | None = None,
    min_count: int = 3,
    top_n: int = 12,
) -> list[dict[str, Any]]:
    """Suggest Chinese strings used where a term's canonical target is absent.

    This is a report for human review, not an automatic glossary edit. A
    candidate must occur in at least ``min_count`` affected segments and must not
    already be canonical or forbidden.
    """
    book = Path(book_dir)
    glossary = terms if terms is not None else book_state.load_glossary_terms(book)
    index = build_alignment_terms(glossary)
    chapters = chapters if chapters is not None else _chapters(book)
    all_forms: set[str] = set()
    all_forbidden: set[str] = set()
    for term in index:
        all_forms.update(term.canonical_forms)
        all_forbidden.update(term.forbidden)
    suggestions: list[dict[str, Any]] = []
    for term in index:
        if not term.canonical_forms:
            continue
        counts: dict[str, int] = {}
        segments: dict[str, set[tuple[Any, Any]]] = {}
        for chapter in chapters:
            for segment in chapter.get("segments") or []:
                if segment.get("kind") == "heading":
                    continue
                source = segment.get("source") or ""
                target = segment.get("target") or ""
                if not source or not target.strip() or not _term_matches(term, source):
                    continue
                if any(_contains(target, form) for form in term.canonical_forms):
                    continue
                if _first_variant(term, target, all_forms):
                    continue
                for candidate in _chinese_candidates(target):
                    if any(candidate in form or form in candidate for form in term.canonical_forms):
                        continue
                    if any(candidate in variant or variant in candidate for variant in term.forbidden):
                        continue
                    if any(
                        candidate in form or form in candidate for form in all_forms
                    ):
                        continue
                    if any(
                        candidate in variant or variant in candidate
                        for variant in all_forbidden
                    ):
                        continue
                    counts[candidate] = counts.get(candidate, 0) + 1
                    segments.setdefault(candidate, set()).add(
                        (chapter.get("chapter"), segment.get("index"))
                    )
        ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
        for candidate, count in ranked[:top_n]:
            if count < min_count:
                continue
            suggestions.append(
                {
                    "term": term.term_id,
                    "canonical": term.target,
                    "candidate": candidate,
                    "count": count,
                    "segments": [
                        {"chapter": chapter, "index": index}
                        for chapter, index in sorted(segments.get(candidate, set()))[:5]
                    ],
                }
            )
    return suggestions


def _replacement_for(term: AlignmentTerm, variant: str) -> str | None:
    if not term.canonical_forms:
        return None
    full = term.canonical_forms[0]
    if any(separator in variant for separator in TARGET_SEPARATORS):
        return full
    components = [form for form in term.canonical_forms if form != full]
    if not components:
        return full
    length = len(variant)
    distances = {abs(len(component) - length) for component in components}
    closest = [component for component in components if abs(len(component) - length) == min(distances)]
    if len(closest) != 1:
        return None
    return closest[0]


def _replace_variant_in_text(
    text: str, variant: str, term: AlignmentTerm, replacement: str
) -> tuple[str, int]:
    """Replace a variant without duplicating an already-present canonical prefix."""
    full = term.canonical_forms[0] if term.canonical_forms else replacement
    result: list[str] = []
    start = 0
    count = 0
    while True:
        index = text.find(variant, start)
        if index < 0:
            break
        prefix = ""
        if replacement == full:
            before = text[:index]
            for size in range(min(len(full) - 1, len(before)), 0, -1):
                if before[-size:] == full[:size]:
                    prefix = full[:size]
                    break
        result.append(text[start:index])
        result.append(full[len(prefix):] if prefix else replacement)
        start = index + len(variant)
        count += 1
    result.append(text[start:])
    return "".join(result), count


def apply_forbidden_replacements(
    book_dir: Path,
    terms: list[Any] | None = None,
    chapters: list[dict[str, Any]] | None = None,
    *,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Replace confirmed forbidden variants with canonical forms.

    Replacement is deliberately conservative: a variant containing a name
    separator becomes the full canonical target; a bare variant becomes the
    canonical component with the closest length. Unresolved forms are reported
    and left unchanged.
    """
    book = Path(book_dir)
    glossary = terms if terms is not None else book_state.load_glossary_terms(book)
    index = build_alignment_terms(glossary)
    protected = protected_canonical_forms(index)
    chapters = chapters if chapters is not None else _chapters(book)
    replacements: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    changed_segments = 0
    for chapter in chapters:
        changed = False
        for segment in chapter.get("segments") or []:
            if segment.get("kind") == "heading":
                continue
            target = segment.get("target") or ""
            if not target.strip():
                continue
            updated = target
            source = segment.get("source") or ""
            for term in index:
                if not _term_matches(term, source):
                    continue
                for variant in sorted(term.forbidden, key=len, reverse=True):
                    if not variant or variant in term.canonical_forms or variant not in updated:
                        continue
                    if any(variant in form and _contains(updated, form) for form in protected):
                        continue
                    replacement = _replacement_for(term, variant)
                    if replacement is None:
                        unresolved.append(
                            {
                                "term": term.term_id,
                                "variant": variant,
                                "chapter": chapter.get("chapter"),
                                "index": segment.get("index"),
                            }
                        )
                        continue
                    updated, count = _replace_variant_in_text(
                        updated, variant, term, replacement
                    )
                    if not count:
                        continue
                    replacements.append(
                        {
                            "term": term.term_id,
                            "variant": variant,
                            "replacement": replacement,
                            "count": count,
                            "chapter": chapter.get("chapter"),
                            "index": segment.get("index"),
                        }
                    )
            if updated != target:
                changed = True
                changed_segments += 1
                if not dry_run:
                    segment["target"] = updated
        if changed and not dry_run:
            segment_state.save_chapter(segment_state.chapter_path(book, chapter["chapter"]), chapter)
    return {
        "dry_run": dry_run,
        "replacements": replacements,
        "unresolved": unresolved,
        "changed_segments": changed_segments,
        "counts": {
            "replacements": len(replacements),
            "unresolved": len(unresolved),
            "changed_segments": changed_segments,
        },
    }


def restore_targets_from_archive(
    book_dir: Path, archive_dir: Path
) -> dict[str, Any]:
    """Restore targets segment-by-segment from an archived chapter snapshot.

    A segment is restored only when both ``index`` and ``source_hash`` match the
    archive, so a stale or partial archive cannot overwrite unrelated text.
    """
    book = Path(book_dir)
    archive = Path(archive_dir)
    source_dir = archive / "source" / segment_state.CHAPTERS_DIR
    archive_chapters = source_dir if source_dir.is_dir() else archive
    current_dir = book / "source" / segment_state.CHAPTERS_DIR
    restored = skipped = 0
    details: list[dict[str, Any]] = []
    for path in sorted(current_dir.glob("ch-*.json")):
        archive_path = archive_chapters / path.name
        if not archive_path.is_file():
            continue
        current = segment_state.load_chapter(path)
        old = segment_state.load_chapter(archive_path)
        by_key = {
            (segment.get("index"), segment.get("source_hash")): segment
            for segment in old.get("segments") or []
        }
        changed = False
        for segment in current.get("segments") or []:
            key = (segment.get("index"), segment.get("source_hash"))
            archived = by_key.get(key)
            target = archived.get("target") if archived else None
            if not isinstance(target, str) or not target.strip():
                skipped += 1
                continue
            if segment.get("target") == target:
                continue
            segment["target"] = target
            changed = True
            restored += 1
            details.append(
                {"chapter": current.get("chapter"), "index": segment.get("index")}
            )
        if changed:
            segment_state.save_chapter(path, current)
    return {"restored": restored, "skipped": skipped, "details": details}


def report(
    book_dir: Path,
    *,
    strict_coverage: bool = False,
    min_count: int = 2,
) -> dict[str, Any]:
    """Build a combined alignment/coverage report."""
    book = Path(book_dir)
    glossary = book_state.load_glossary_terms(book)
    chapters = _chapters(book)
    alignment = scan_alignment(book, glossary, chapters)
    coverage = coverage_report(book, glossary, chapters, min_count=min_count)
    errors = list(alignment["errors"])
    if strict_coverage:
        errors.extend(
            {"kind": "glossary_coverage", **item} for item in coverage["missing"]
        )
    return {
        "book_dir": str(book),
        "ok": not errors,
        "errors": errors,
        "warnings": alignment["warnings"],
        "coverage": coverage,
        "counts": {
            **alignment["counts"],
            "coverage_missing": len(coverage["missing"]),
        },
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("book_dir", type=Path)
    parser.add_argument("--json", action="store_true", help="print the full report")
    parser.add_argument(
        "--strict-coverage",
        action="store_true",
        help="treat uncovered source proper nouns as errors",
    )
    parser.add_argument(
        "--min-count",
        type=int,
        default=2,
        help="minimum occurrences for a coverage candidate (default: 2)",
    )
    parser.add_argument(
        "--discover",
        action="store_true",
        help="print candidate Chinese variants for human review",
    )
    parser.add_argument(
        "--apply-forbidden",
        action="store_true",
        help="replace confirmed forbidden variants in segment targets",
    )
    parser.add_argument(
        "--restore-from-archive",
        type=Path,
        help="restore targets by index + source_hash from an archived book snapshot",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="with --apply-forbidden, report replacements without writing",
    )
    args = parser.parse_args(argv)
    if args.restore_from_archive:
        result = restore_targets_from_archive(args.book_dir, args.restore_from_archive)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    if args.apply_forbidden:
        result = apply_forbidden_replacements(args.book_dir, dry_run=args.dry_run)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    if args.discover:
        terms = book_state.load_glossary_terms(args.book_dir)
        suggestions = discover_variants(args.book_dir, terms=terms)
        print(json.dumps(suggestions, ensure_ascii=False, indent=2))
        return
    result = report(
        args.book_dir,
        strict_coverage=args.strict_coverage,
        min_count=args.min_count,
    )
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(
            json.dumps(
                {
                    "ok": result["ok"],
                    "errors": len(result["errors"]),
                    "warnings": len(result["warnings"]),
                    "coverage_missing": result["counts"]["coverage_missing"],
                    "variant_errors": result["counts"]["variant_errors"],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    if not result["ok"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
