#!/usr/bin/env python3
"""Load the active translation profile (default: Harry Potter fanfiction).

A profile is a directory under ``profiles/`` with a ``manifest.json`` and
Markdown rule files. The manifest maps logical sections to files, so tools can
share one source of truth instead of duplicating setting-specific rules in
each prompt.

Resolution order:
1. ``MALFOID_PROFILE`` environment variable (profile id);
2. the ``profile`` field in ``<work>/meta.json`` when a work directory is given;
3. ``hp-fanfic``.

Usage:
    from prompt_profile import load_profile, section
    rules = section("translation")
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PROFILES = ROOT / "profiles"
SKILLS = ROOT / ".pi" / "skills"
DEFAULT_PROFILE = "hp-fanfic"


def profile_id(work_dir: Path | None = None) -> str:
    """Resolve the active profile id without touching the network or model."""
    env = os.environ.get("MALFOID_PROFILE", "").strip()
    if env:
        return env
    if work_dir is not None:
        meta_path = Path(work_dir) / "meta.json"
        if meta_path.is_file():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                meta = {}
            value = meta.get("profile")
            if isinstance(value, str) and value.strip():
                return value.strip()
    return DEFAULT_PROFILE


def profile_dir(name: str | None = None, work_dir: Path | None = None) -> Path:
    """Return the profile directory, failing clearly when it does not exist."""
    directory = PROFILES / (name or profile_id(work_dir))
    if not (directory / "manifest.json").is_file():
        raise FileNotFoundError(f"unknown translation profile: {directory}")
    return directory


def load_profile(name: str | None = None, work_dir: Path | None = None) -> dict[str, Any]:
    """Load and minimally validate the profile manifest."""
    directory = profile_dir(name, work_dir)
    data = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    if not isinstance(data.get("id"), str) or not isinstance(data.get("files"), dict):
        raise ValueError(f"invalid profile manifest: {directory}")
    return data


def section(key: str, name: str | None = None, work_dir: Path | None = None) -> str:
    """Read one rule section from the active profile."""
    manifest = load_profile(name, work_dir)
    relative = manifest["files"].get(key)
    if not relative:
        raise KeyError(f"profile {manifest['id']} has no section: {key}")
    directory = profile_dir(name, work_dir)
    path = directory / relative
    if not path.is_file():
        raise FileNotFoundError(f"profile section file missing: {path}")
    return path.read_text(encoding="utf-8").strip()


def compose(base: str, key: str, name: str | None = None, work_dir: Path | None = None) -> str:
    """Append a profile section to an existing system/rules prompt."""
    extra = section(key, name, work_dir)
    return f"{base.rstrip()}\n\n{extra}\n"


def skill_name(key: str, name: str | None = None, work_dir: Path | None = None) -> str | None:
    """Return the skill configured for a profile section, if any."""
    manifest = load_profile(name, work_dir)
    skills = manifest.get("skills")
    if not isinstance(skills, dict):
        return None
    value = skills.get(key)
    return value.strip() if isinstance(value, str) and value.strip() else None


def skill_text(name: str) -> str:
    """Read a vendored skill's SKILL.md without its YAML frontmatter."""
    path = SKILLS / name / "SKILL.md"
    if not path.is_file():
        raise FileNotFoundError(f"skill file missing: {path}")
    text = path.read_text(encoding="utf-8").strip()
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) == 3:
            text = parts[2].strip()
    return text


def compose_skill(skill: str, project_rules: str, mode: str = "轻改") -> str:
    """Compose a skill in a fixed mode, with project rules as the final override."""
    text = skill_text(skill)
    header = (
        f"使用 {skill} skill。模式：{mode}。只执行 {mode} 模式；"
        "不执行精修、重写、续写、审稿等其他模式。"
    )
    overrides = "PROJECT OVERRIDES（最高优先级；与 skill 冲突时以项目规则为准）："
    return "\n\n".join([header, text, overrides, project_rules.rstrip()])


def section_with_skill(
    key: str,
    mode: str = "轻改",
    name: str | None = None,
    work_dir: Path | None = None,
) -> str:
    """Read a profile section and prepend its configured skill in a fixed mode."""
    project_rules = section(key, name, work_dir)
    configured = skill_name(key, name, work_dir)
    if not configured:
        return project_rules
    return compose_skill(configured, project_rules, mode)


def forbidden_names(name: str | None = None, work_dir: Path | None = None) -> list[str]:
    """Return names that must never appear in a translation."""
    manifest = load_profile(name, work_dir)
    value = manifest.get("forbidden_names")
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


def female_draco(name: str | None = None, work_dir: Path | None = None) -> dict[str, Any]:
    """Return the female Draco naming/pronoun contract for checks."""
    manifest = load_profile(name, work_dir)
    value = manifest.get("female_draco")
    return value if isinstance(value, dict) else {}


def foreign_text_allowlist(name: str | None = None, work_dir: Path | None = None) -> list[str]:
    """Return canonical foreign-language phrases that checks must not flag."""
    manifest = load_profile(name, work_dir)
    value = manifest.get("foreign_text_allowlist")
    return [item for item in value if isinstance(item, str) and item] if isinstance(value, list) else []
