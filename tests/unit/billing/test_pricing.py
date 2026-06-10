"""
Unit tests for roboco.billing.pricing — calculate_cost().

Covers:
- Each model tier (opus, sonnet, haiku) with all 4 token types.
- Unknown model name returns 0.0 without raising.
- Empty model string returns 0.0 without raising.
- Substring match correctness: longer fragment wins
  (e.g. 'claude-sonnet-4-6' matches 'claude-sonnet-4' not bare 'sonnet').
"""

from __future__ import annotations

import pytest
from roboco.billing.pricing import calculate_cost

# ---------------------------------------------------------------------------
# Named constants (ruff PLR2004: magic values in comparisons must be named).
# ---------------------------------------------------------------------------

# Token counts
_M = 1_000_000  # 1 million tokens

# Pricing — per-1M USD, matches the _PRICING table in pricing.py
_OPUS_INPUT = 5.00
_OPUS_OUTPUT = 25.00
_OPUS_CACHE_READ = 0.50
_OPUS_CACHE_WRITE = 6.25

_SONNET_INPUT = 3.00
_SONNET_OUTPUT = 15.00
_SONNET_CACHE_READ = 0.30
_SONNET_CACHE_WRITE = 0.75

_HAIKU_INPUT = 1.00
_HAIKU_OUTPUT = 5.00
_HAIKU_CACHE_READ = 0.10
_HAIKU_CACHE_WRITE = 1.25

_HAIKU3_INPUT = 0.25  # claude-haiku-3 is cheaper than haiku-3-5 / haiku-4

# Tolerance for floating-point comparisons
_TOL = 1e-4


# ---------------------------------------------------------------------------
# Opus tier
# ---------------------------------------------------------------------------


class TestOpusTier:
    """claude-opus-4 family pricing."""

    def test_input_only(self) -> None:
        cost = calculate_cost("claude-opus-4-5", tokens_input=_M, tokens_output=0)
        assert abs(cost - _OPUS_INPUT) < _TOL

    def test_output_only(self) -> None:
        cost = calculate_cost("claude-opus-4-5", tokens_input=0, tokens_output=_M)
        assert abs(cost - _OPUS_OUTPUT) < _TOL

    def test_cache_read_only(self) -> None:
        cost = calculate_cost(
            "claude-opus-4-5",
            tokens_input=0,
            tokens_output=0,
            tokens_cache_read=_M,
        )
        assert abs(cost - _OPUS_CACHE_READ) < _TOL

    def test_cache_write_only(self) -> None:
        cost = calculate_cost(
            "claude-opus-4-5",
            tokens_input=0,
            tokens_output=0,
            tokens_cache_write=_M,
        )
        assert abs(cost - _OPUS_CACHE_WRITE) < _TOL

    def test_all_token_types(self) -> None:
        cost = calculate_cost(
            "claude-opus-4-5",
            tokens_input=_M,
            tokens_output=_M,
            tokens_cache_read=_M,
            tokens_cache_write=_M,
        )
        expected = _OPUS_INPUT + _OPUS_OUTPUT + _OPUS_CACHE_READ + _OPUS_CACHE_WRITE
        assert abs(cost - expected) < _TOL

    def test_short_alias(self) -> None:
        """Bare 'opus' alias resolves to the opus tier."""
        cost = calculate_cost("opus", tokens_input=_M, tokens_output=0)
        assert abs(cost - _OPUS_INPUT) < _TOL

    def test_returns_float(self) -> None:
        cost = calculate_cost("claude-opus-4", tokens_input=100, tokens_output=50)
        assert isinstance(cost, float)


# ---------------------------------------------------------------------------
# Sonnet tier
# ---------------------------------------------------------------------------


class TestSonnetTier:
    """claude-sonnet-4 family pricing."""

    def test_input_only(self) -> None:
        cost = calculate_cost("claude-sonnet-4-6", tokens_input=_M, tokens_output=0)
        assert abs(cost - _SONNET_INPUT) < _TOL

    def test_output_only(self) -> None:
        cost = calculate_cost("claude-sonnet-4-6", tokens_input=0, tokens_output=_M)
        assert abs(cost - _SONNET_OUTPUT) < _TOL

    def test_cache_read_only(self) -> None:
        cost = calculate_cost(
            "claude-sonnet-4-6",
            tokens_input=0,
            tokens_output=0,
            tokens_cache_read=_M,
        )
        assert abs(cost - _SONNET_CACHE_READ) < _TOL

    def test_cache_write_only(self) -> None:
        cost = calculate_cost(
            "claude-sonnet-4-6",
            tokens_input=0,
            tokens_output=0,
            tokens_cache_write=_M,
        )
        assert abs(cost - _SONNET_CACHE_WRITE) < _TOL

    def test_all_token_types(self) -> None:
        cost = calculate_cost(
            "claude-sonnet-4-6",
            tokens_input=_M,
            tokens_output=_M,
            tokens_cache_read=_M,
            tokens_cache_write=_M,
        )
        expected = (
            _SONNET_INPUT + _SONNET_OUTPUT + _SONNET_CACHE_READ + _SONNET_CACHE_WRITE
        )
        assert abs(cost - expected) < _TOL

    def test_short_alias(self) -> None:
        """Bare 'sonnet' alias resolves to the sonnet tier."""
        cost = calculate_cost("sonnet", tokens_input=_M, tokens_output=0)
        assert abs(cost - _SONNET_INPUT) < _TOL

    def test_35_variant(self) -> None:
        """claude-3-5-sonnet resolves to sonnet tier."""
        cost = calculate_cost(
            "claude-3-5-sonnet-20241022", tokens_input=_M, tokens_output=0
        )
        assert abs(cost - _SONNET_INPUT) < _TOL


# ---------------------------------------------------------------------------
# Haiku tier
# ---------------------------------------------------------------------------


class TestHaikuTier:
    """claude-haiku family pricing."""

    def test_input_only(self) -> None:
        cost = calculate_cost("claude-haiku-4-5", tokens_input=_M, tokens_output=0)
        assert abs(cost - _HAIKU_INPUT) < _TOL

    def test_output_only(self) -> None:
        cost = calculate_cost("claude-haiku-4-5", tokens_input=0, tokens_output=_M)
        assert abs(cost - _HAIKU_OUTPUT) < _TOL

    def test_cache_read_only(self) -> None:
        cost = calculate_cost(
            "claude-haiku-4-5",
            tokens_input=0,
            tokens_output=0,
            tokens_cache_read=_M,
        )
        assert abs(cost - _HAIKU_CACHE_READ) < _TOL

    def test_cache_write_only(self) -> None:
        cost = calculate_cost(
            "claude-haiku-4-5",
            tokens_input=0,
            tokens_output=0,
            tokens_cache_write=_M,
        )
        assert abs(cost - _HAIKU_CACHE_WRITE) < _TOL

    def test_all_token_types(self) -> None:
        cost = calculate_cost(
            "claude-haiku-4-5",
            tokens_input=_M,
            tokens_output=_M,
            tokens_cache_read=_M,
            tokens_cache_write=_M,
        )
        expected = _HAIKU_INPUT + _HAIKU_OUTPUT + _HAIKU_CACHE_READ + _HAIKU_CACHE_WRITE
        assert abs(cost - expected) < _TOL

    def test_short_alias(self) -> None:
        """Bare 'haiku' alias resolves to the haiku tier."""
        cost = calculate_cost("haiku", tokens_input=_M, tokens_output=0)
        assert abs(cost - _HAIKU_INPUT) < _TOL

    def test_haiku3_variant(self) -> None:
        """claude-haiku-3 has lower pricing than haiku-3-5."""
        cost = calculate_cost("claude-haiku-3", tokens_input=_M, tokens_output=0)
        assert abs(cost - _HAIKU3_INPUT) < _TOL


# ---------------------------------------------------------------------------
# Unknown / edge cases — must return 0.0 without raising
# ---------------------------------------------------------------------------


class TestUnknownModels:
    def test_unknown_model_name_returns_zero(self) -> None:
        cost = calculate_cost("gpt-4o", tokens_input=_M, tokens_output=_M)
        assert cost == 0.0

    def test_empty_string_returns_zero(self) -> None:
        cost = calculate_cost("", tokens_input=_M, tokens_output=_M)
        assert cost == 0.0

    def test_gibberish_returns_zero(self) -> None:
        cost = calculate_cost(
            "totally-unknown-model-xyz", tokens_input=100, tokens_output=100
        )
        assert cost == 0.0

    def test_zero_tokens_with_unknown_model_returns_zero(self) -> None:
        cost = calculate_cost("unknown", tokens_input=0, tokens_output=0)
        assert cost == 0.0

    def test_does_not_raise_on_unknown_model(self) -> None:
        """Must not raise regardless of token counts."""
        try:
            calculate_cost(
                "not-a-claude-model",
                tokens_input=999_999,
                tokens_output=999_999,
            )
        except Exception as exc:
            pytest.fail(f"calculate_cost raised unexpectedly: {exc}")


# ---------------------------------------------------------------------------
# Substring match correctness
# ---------------------------------------------------------------------------

# Named constants for the comparison floor/ceiling used in these tests.
_ZERO_COST = 0.0
_SONNET_CHEAPER_THAN_OPUS = True  # structural assertion in the test below


class TestSubstringMatchPriority:
    def test_claude_sonnet_4_resolves_non_zero(self) -> None:
        """'claude-sonnet-4-6' must find a match (non-zero cost)."""
        cost = calculate_cost("claude-sonnet-4-6", tokens_input=_M, tokens_output=0)
        assert cost > _ZERO_COST

    def test_haiku3_cheaper_than_haiku4(self) -> None:
        """claude-haiku-3 is cheaper than claude-haiku-4 — longest-match wins."""
        haiku3_cost = calculate_cost("claude-haiku-3", tokens_input=_M, tokens_output=0)
        haiku4_cost = calculate_cost("claude-haiku-4", tokens_input=_M, tokens_output=0)
        # haiku-3 ($0.25/1M) < haiku-4 ($1.00/1M)
        assert haiku3_cost < haiku4_cost

    def test_non_claude_model_returns_zero(self) -> None:
        """A random non-Claude model must not match any Claude pricing entry."""
        non_opus_cost = calculate_cost("llama-3-70b", tokens_input=_M, tokens_output=0)
        assert non_opus_cost == _ZERO_COST

    def test_opus_model_non_zero(self) -> None:
        """Claude opus model resolves to non-zero cost."""
        opus_cost = calculate_cost("claude-opus-4", tokens_input=_M, tokens_output=0)
        assert opus_cost > _ZERO_COST

    def test_zero_tokens_returns_zero_for_known_model(self) -> None:
        """Known model with 0 tokens has 0 cost."""
        cost = calculate_cost("claude-opus-4", tokens_input=0, tokens_output=0)
        assert cost == _ZERO_COST

    def test_case_insensitive_matching(self) -> None:
        """Model name matching is case-insensitive."""
        lower_cost = calculate_cost(
            "claude-sonnet-4-6", tokens_input=1000, tokens_output=1000
        )
        upper_cost = calculate_cost(
            "CLAUDE-SONNET-4-6", tokens_input=1000, tokens_output=1000
        )
        assert lower_cost == upper_cost
        assert lower_cost > _ZERO_COST
