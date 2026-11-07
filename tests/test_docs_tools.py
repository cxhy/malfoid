"""Documentation guardrail: every pipeline tool is listed in docs/tools.md."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
DOC = ROOT / "docs" / "tools.md"


def test_every_tool_is_documented() -> None:
    text = DOC.read_text(encoding="utf-8")
    missing = [path.name for path in sorted(TOOLS.glob("*.py")) if path.name not in text]
    assert missing == []
