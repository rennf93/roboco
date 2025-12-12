"""
TOON Metrics

Tracks token savings and usage statistics for TOON vs JSON serialization.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime


@dataclass
class ToonMetrics:
    """
    Metrics for tracking TOON serialization efficiency.

    Tracks character counts (as proxy for tokens) for JSON vs TOON
    to measure actual savings in production.
    """

    json_chars: int = 0
    toon_chars: int = 0
    encode_count: int = 0
    decode_count: int = 0
    decode_fallback_count: int = 0
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def savings_percent(self) -> float:
        """Calculate percentage of characters saved using TOON."""
        if self.json_chars == 0:
            return 0.0
        return (1 - self.toon_chars / self.json_chars) * 100

    @property
    def fallback_rate(self) -> float:
        """Calculate rate of fallback to JSON decoding."""
        if self.decode_count == 0:
            return 0.0
        return (self.decode_fallback_count / self.decode_count) * 100

    def record_encode(self, json_chars: int, toon_chars: int) -> None:
        """Record an encode operation with character counts."""
        self.json_chars += json_chars
        self.toon_chars += toon_chars
        self.encode_count += 1

    def record_decode(self, used_fallback: bool = False) -> None:
        """Record a decode operation."""
        self.decode_count += 1
        if used_fallback:
            self.decode_fallback_count += 1

    def to_dict(self) -> dict:
        """Convert metrics to dictionary for logging/reporting."""
        return {
            "json_chars": self.json_chars,
            "toon_chars": self.toon_chars,
            "savings_percent": round(self.savings_percent, 2),
            "encode_count": self.encode_count,
            "decode_count": self.decode_count,
            "fallback_rate": round(self.fallback_rate, 2),
            "started_at": self.started_at.isoformat(),
        }

    def reset(self) -> None:
        """Reset all metrics."""
        self.json_chars = 0
        self.toon_chars = 0
        self.encode_count = 0
        self.decode_count = 0
        self.decode_fallback_count = 0
        self.started_at = datetime.now(UTC)


# Global metrics holder
class _MetricsHolder:
    """Holder for singleton ToonMetrics instance."""

    instance: ToonMetrics | None = None


def get_toon_metrics() -> ToonMetrics:
    """Get the global TOON metrics instance."""
    if _MetricsHolder.instance is None:
        _MetricsHolder.instance = ToonMetrics()
    return _MetricsHolder.instance
