"""Endpoint preflight tests (offline)."""

from __future__ import annotations

import preflight_model


def test_recommend_workers_returns_the_probe_recommendation(monkeypatch) -> None:
    monkeypatch.setattr(
        preflight_model,
        "probe",
        lambda *args, **kwargs: {"mode": "parallel", "recommended_workers": 2},
    )
    assert preflight_model.recommend_workers("model") == 2
