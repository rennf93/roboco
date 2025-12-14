"""
TOON Metrics

Tracks token savings and usage statistics for TOON vs JSON serialization.
"""

from roboco.models.llm import ToonMetrics


# Global metrics holder
class _MetricsHolder:
    """Holder for singleton ToonMetrics instance."""

    instance: ToonMetrics | None = None


def get_toon_metrics() -> ToonMetrics:
    """Get the global TOON metrics instance."""
    if _MetricsHolder.instance is None:
        _MetricsHolder.instance = ToonMetrics()
    return _MetricsHolder.instance
