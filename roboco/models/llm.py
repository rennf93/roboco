"""
LLM Models

Domain types for LLM integration.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime


@dataclass
class ToonConfig:
    """Configuration for TOON encoding."""

    delimiter: str = ","
    indent: int = 2
    include_length: bool = True


@dataclass
class EncodedBlock:
    """A TOON-encoded block with metadata."""

    content: str
    label: str
    token_estimate: int = 0

    def __str__(self) -> str:
        """Return the formatted block."""
        return f"[{self.label}]\n{self.content}"


@dataclass
class LLMUsage:
    """Token usage statistics for an LLM call."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        """Total tokens used."""
        return self.input_tokens + self.output_tokens

    @property
    def total_input_with_cache(self) -> int:
        """Total input including cache operations."""
        return (
            self.input_tokens
            + self.cache_creation_input_tokens
            + self.cache_read_input_tokens
        )


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
