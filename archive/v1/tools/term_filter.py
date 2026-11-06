#!/usr/bin/env python3
"""Select the glossary subset that actually occurs in a piece of text.

Wenyi sends a relevant glossary subset per batch rather than the whole book
glossary. Besides reducing prompt size, this avoids making the model reason
about hundreds of unrelated terms, which is one source of reasoning-budget
overflow on long chunks.
"""

from __future__ import annotations

import re
from typing import Any

ASCII = re.compile(r"^[\x00-\x7f]+$")


def forms(term: dict[str, Any]) -> list[str]:
    """Return the source form plus aliases, ignoring empty entries."""
    values = [term.get("source", "")] + list(term.get("aliases") or [])
    return [value for value in values if isinstance(value, str) and value.strip()]


def appears(form: str, text: str) -> bool:
    """Match Latin forms as whole words and non-Latin forms as substrings."""
    if not form or not text:
        return False
    if ASCII.fullmatch(form):
        return re.search(rf"(?<![A-Za-z]){re.escape(form)}(?![A-Za-z])", text) is not None
    return form in text


def term_in_text(term: dict[str, Any], text: str) -> bool:
    return any(appears(form, text) for form in forms(term))


def select_terms(
    terms: list[dict[str, Any]],
    text: str,
    *,
    check_target: bool = False,
) -> list[dict[str, Any]]:
    """Keep terms whose source/alias occurs in text.

    With ``check_target``, also keep terms whose target translation occurs in the
    text (useful when checking Chinese output against an English glossary).
    """
    selected = []
    for term in terms:
        target = term.get("target", "")
        if term_in_text(term, text) or (
            check_target and isinstance(target, str) and target and target in text
        ):
            selected.append(term)
    return selected


def format_terms(terms: list[dict[str, Any]]) -> str:
    lines = []
    for term in terms:
        source = term.get("source", "")
        target = term.get("target", "")
        if not source or not target:
            continue
        line = f"- {source} → {target}"
        aliases = term.get("aliases") or []
        if aliases:
            line += f"（别名：{', '.join(aliases)}）"
        notes = term.get("notes", "")
        if notes:
            line += f"；{notes}"
        lines.append(line)
    return "\n".join(lines)
