"""Local OpenAI-compatible model endpoint for the v2 pipeline.

Environment variables (MALFOID_BASE_URL / MALFOID_API_KEY) take precedence;
otherwise the local pi model registry is used. This module is the v2 model
boundary and replaces the legacy translate_chunk adapter.
"""

from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path

MODELS = Path.home() / ".pi/agent/models.json"
PROVIDER = "deepseek-v4-1-flash"

SYSTEM = (
    "你是一位中文文学译者，专门把英文同人小说译成简体中文。"
    "译文要读起来像中文原创小说，而不是英文句子的逐字替换。"
    "你只输出译文正文，不写任何说明、前言、后记或译注。"
)


def load_config() -> tuple[str, str]:
    base = os.environ.get("MALFOID_BASE_URL")
    if base:
        return base.rstrip("/"), os.environ.get("MALFOID_API_KEY", "")
    cfg = json.loads(MODELS.read_text(encoding="utf-8"))["providers"][PROVIDER]
    return cfg["baseUrl"].rstrip("/"), cfg["apiKey"]


def call(
    base_url: str, api_key: str, model: str, prompt: str, effort: str, max_tokens: int
) -> tuple[str, dict]:
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.6,
        "max_tokens": max_tokens,
        "reasoning_effort": effort,
    }
    req = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
    )
    with urllib.request.urlopen(req, timeout=300) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    text = payload["choices"][0]["message"].get("content") or ""
    usage = payload.get("usage") or {}
    usage["finish_reason"] = payload["choices"][0].get("finish_reason")
    return text, usage


def make_client(model: str, effort: str, max_tokens: int):
    base_url, api_key = load_config()

    def client(prompt: str) -> str:
        text, _ = call(base_url, api_key, model, prompt, effort, max_tokens)
        return text

    return client
