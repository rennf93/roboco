"""Coverage for roboco.llm.metrics — singleton ToonMetrics holder."""

from __future__ import annotations

from roboco.llm.metrics import _MetricsHolder, get_toon_metrics


def test_get_toon_metrics_returns_singleton() -> None:
    _MetricsHolder.instance = None
    a = get_toon_metrics()
    b = get_toon_metrics()
    assert a is b


def test_get_toon_metrics_creates_when_unset() -> None:
    _MetricsHolder.instance = None
    metrics = get_toon_metrics()
    assert metrics is not None
