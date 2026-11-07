"""Model client boundary tests (offline)."""

from __future__ import annotations

import json
from pathlib import Path

import model_api


def test_make_client_writes_usage_log(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(model_api, "load_config", lambda: ("http://example.invalid", "key"))
    monkeypatch.setattr(
        model_api,
        "call",
        lambda *args: ("译文", {"total_tokens": 7, "finish_reason": "stop"}),
    )
    log = tmp_path / "usage.jsonl"
    client = model_api.make_client("model", "low", 1000, usage_log=log)
    assert client("prompt") == "译文"
    rows = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    assert rows[0]["usage"]["total_tokens"] == 7
    assert rows[0]["prompt_chars"] == len("prompt")
    assert rows[0]["duration"] >= 0


def test_make_client_without_usage_log_writes_nothing(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(model_api, "load_config", lambda: ("http://example.invalid", "key"))
    client = model_api.make_client("model", "low", 1000)
    assert callable(client)
    assert list(tmp_path.iterdir()) == []
