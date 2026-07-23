"""
Unit tests for roboco.billing.pricing — calculate_cost().

Covers:
- Each model tier (opus, sonnet, haiku) with all 4 token types.
- Unknown model name returns 0.0 without raising.
- Empty model string returns 0.0 without raising.
- Substring match correctness: longer fragment wins
  (e.g. 'claude-sonnet-4-6' matches 'claude-sonnet-4' not bare 'sonnet';
  'claude-sonnet-5' matches its own promo entry).
"""

from __future__ import annotations

from datetime import date

import pytest
from roboco.billing import pricing as p
from roboco.billing.pricing import (
    CostResult,
    _is_anthropic_model,
    calculate_cost,
    calculate_cost_result,
    input_price_per_million,
)

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

# Sonnet 5 — promotional pricing (33% off Sonnet 4.6, pay 67%) through 2026-08-31
_SONNET5_INPUT = 2.01
_SONNET5_OUTPUT = 10.05
_SONNET5_CACHE_READ = 0.201
_SONNET5_CACHE_WRITE = 0.5025

_HAIKU_INPUT = 1.00
_HAIKU_OUTPUT = 5.00
_HAIKU_CACHE_READ = 0.10
_HAIKU_CACHE_WRITE = 1.25

_HAIKU3_INPUT = 0.25  # claude-haiku-3 is cheaper than haiku-3-5 / haiku-4

# xAI Grok — priced non-Anthropic (per the xAI API)
_GROK_INPUT = 1.00
_GROK_OUTPUT = 2.00
_GROK_CACHE_READ = 0.20
_GROK_CACHE_WRITE = 1.00

# OpenAI Codex — priced non-Anthropic (ChatGPT-subscription CLI, priced here
# for cost attribution)
_CODEX_INPUT = 1.75
_CODEX_OUTPUT = 14.00
_CODEX_CACHE_READ = 0.175
_CODEX_CACHE_WRITE = 1.75

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
    """claude-sonnet-4 family pricing (full rate — pre-promo / historical)."""

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


class TestSonnet5PromoTier:
    """claude-sonnet-5 promotional pricing — 33% off Sonnet 4.6 (through
    2026-08-31). A dedicated table entry wins over the bare 'sonnet' fragment."""

    def test_input_only(self) -> None:
        cost = calculate_cost("claude-sonnet-5", tokens_input=_M, tokens_output=0)
        assert abs(cost - _SONNET5_INPUT) < _TOL

    def test_output_only(self) -> None:
        cost = calculate_cost("claude-sonnet-5", tokens_input=0, tokens_output=_M)
        assert abs(cost - _SONNET5_OUTPUT) < _TOL

    def test_cache_read_only(self) -> None:
        cost = calculate_cost(
            "claude-sonnet-5", tokens_input=0, tokens_output=0, tokens_cache_read=_M
        )
        assert abs(cost - _SONNET5_CACHE_READ) < _TOL

    def test_cache_write_only(self) -> None:
        cost = calculate_cost(
            "claude-sonnet-5", tokens_input=0, tokens_output=0, tokens_cache_write=_M
        )
        assert abs(cost - _SONNET5_CACHE_WRITE) < _TOL

    def test_all_token_types(self) -> None:
        cost = calculate_cost(
            "claude-sonnet-5",
            tokens_input=_M,
            tokens_output=_M,
            tokens_cache_read=_M,
            tokens_cache_write=_M,
        )
        expected = (
            _SONNET5_INPUT
            + _SONNET5_OUTPUT
            + _SONNET5_CACHE_READ
            + _SONNET5_CACHE_WRITE
        )
        assert abs(cost - expected) < _TOL

    def test_cheaper_than_sonnet4(self) -> None:
        """The promo must actually be cheaper than full Sonnet 4.6."""
        five = calculate_cost("claude-sonnet-5", tokens_input=_M, tokens_output=_M)
        four = calculate_cost("claude-sonnet-4-6", tokens_input=_M, tokens_output=_M)
        assert five < four

    def test_dated_variant_matches_promo(self) -> None:
        """A dated 'claude-sonnet-5-*' id still resolves to the promo entry."""
        cost = calculate_cost(
            "claude-sonnet-5-20260930", tokens_input=_M, tokens_output=0
        )
        assert abs(cost - _SONNET5_INPUT) < _TOL


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
# Grok tier (xAI — priced non-Anthropic)
# ---------------------------------------------------------------------------


class TestGrokTier:
    """grok-build-0.1 pricing — a non-Anthropic model that IS billed per token."""

    def test_input_only(self) -> None:
        cost = calculate_cost("grok-build-0.1", tokens_input=_M, tokens_output=0)
        assert abs(cost - _GROK_INPUT) < _TOL

    def test_output_only(self) -> None:
        cost = calculate_cost("grok-build-0.1", tokens_input=0, tokens_output=_M)
        assert abs(cost - _GROK_OUTPUT) < _TOL

    def test_cached_input(self) -> None:
        cost = calculate_cost(
            "grok-build-0.1", tokens_input=0, tokens_output=0, tokens_cache_read=_M
        )
        assert abs(cost - _GROK_CACHE_READ) < _TOL

    def test_all_token_types(self) -> None:
        cost = calculate_cost(
            "grok-build-0.1",
            tokens_input=_M,
            tokens_output=_M,
            tokens_cache_read=_M,
            tokens_cache_write=_M,
        )
        expected = _GROK_INPUT + _GROK_OUTPUT + _GROK_CACHE_READ + _GROK_CACHE_WRITE
        assert abs(cost - expected) < _TOL

    def test_grok_is_not_treated_as_anthropic(self) -> None:
        """Priced, but not an Anthropic model (no warn-on-unpriced path)."""
        assert _is_anthropic_model("grok-build-0.1") is False
        # Still resolves to a real (non-zero) per-token cost.
        assert calculate_cost("grok-build-0.1", tokens_input=_M, tokens_output=0) > 0.0


# ---------------------------------------------------------------------------
# Codex tier (OpenAI — priced non-Anthropic)
# ---------------------------------------------------------------------------


class TestCodexTier:
    """gpt-5.3-codex pricing — a real input/output split, unlike grok's fold."""

    def test_input_only(self) -> None:
        cost = calculate_cost("gpt-5.3-codex", tokens_input=_M, tokens_output=0)
        assert abs(cost - _CODEX_INPUT) < _TOL

    def test_output_only(self) -> None:
        cost = calculate_cost("gpt-5.3-codex", tokens_input=0, tokens_output=_M)
        assert abs(cost - _CODEX_OUTPUT) < _TOL

    def test_cached_input(self) -> None:
        cost = calculate_cost(
            "gpt-5.3-codex", tokens_input=0, tokens_output=0, tokens_cache_read=_M
        )
        assert abs(cost - _CODEX_CACHE_READ) < _TOL

    def test_cache_write(self) -> None:
        cost = calculate_cost(
            "gpt-5.3-codex", tokens_input=0, tokens_output=0, tokens_cache_write=_M
        )
        assert abs(cost - _CODEX_CACHE_WRITE) < _TOL

    def test_all_token_types(self) -> None:
        cost = calculate_cost(
            "gpt-5.3-codex",
            tokens_input=_M,
            tokens_output=_M,
            tokens_cache_read=_M,
            tokens_cache_write=_M,
        )
        expected = _CODEX_INPUT + _CODEX_OUTPUT + _CODEX_CACHE_READ + _CODEX_CACHE_WRITE
        assert abs(cost - expected) < _TOL

    def test_codex_is_not_treated_as_anthropic(self) -> None:
        assert _is_anthropic_model("gpt-5.3-codex") is False
        assert calculate_cost("gpt-5.3-codex", tokens_input=_M, tokens_output=0) > 0.0

    def test_output_is_pricier_than_input(self) -> None:
        # Codex's real split makes output 8x input — the property grok's
        # single-total fold structurally cannot express.
        assert _CODEX_OUTPUT > _CODEX_INPUT


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
            "claude-sonnet-5", tokens_input=1000, tokens_output=1000
        )
        upper_cost = calculate_cost(
            "CLAUDE-SONNET-5", tokens_input=1000, tokens_output=1000
        )
        assert lower_cost == upper_cost
        assert lower_cost > _ZERO_COST


# ---------------------------------------------------------------------------
# Provider awareness — non-Anthropic models have no per-token cost
# ---------------------------------------------------------------------------


class TestProviderAwareness:
    """Non-Anthropic models (local Ollama / Ollama Cloud) cost 0.0 per token."""

    def test_ollama_prefixed_model_returns_zero(self) -> None:
        """Self-hosted Ollama models (``ollama/`` prefix) have no API cost."""
        cost = calculate_cost("ollama/llama3", tokens_input=_M, tokens_output=_M)
        assert cost == _ZERO_COST

    def test_ollama_cloud_model_returns_zero(self) -> None:
        """Ollama Cloud (``:cloud`` tag) is subscription-billed, not per token."""
        cost = calculate_cost("glm-5.2:cloud", tokens_input=_M, tokens_output=_M)
        assert cost == _ZERO_COST

    def test_bare_local_model_returns_zero(self) -> None:
        """A bare local embedding model has no per-token cost."""
        cost = calculate_cost("qwen3-embedding:0.6b", tokens_input=_M, tokens_output=0)
        assert cost == _ZERO_COST

    def test_is_anthropic_model_true_for_claude_names(self) -> None:
        for name in ("claude-opus-4-6", "claude-fable-5", "opus", "sonnet", "haiku"):
            assert _is_anthropic_model(name) is True, name

    def test_is_anthropic_model_false_for_non_claude_names(self) -> None:
        for name in ("ollama/llama3", "glm-5.2:cloud", "qwen3-embedding", "gpt-4o"):
            assert _is_anthropic_model(name) is False, name


# ---------------------------------------------------------------------------
# Structured cost result — distinguish unpriced Anthropic from genuinely-free
# (#65). ``calculate_cost`` keeps returning a plain float for existing callers;
# ``calculate_cost_result`` returns a ``CostResult`` so a caller can tell real
# spend we failed to price ($0, unpriced=True) apart from local inference
# ($0, unpriced=False).
# ---------------------------------------------------------------------------


class TestCostResult:
    def test_unpriced_anthropic_model_is_flagged(self) -> None:
        result = calculate_cost_result(
            "claude-brand-new-unpriced", tokens_input=_M, tokens_output=0
        )
        assert isinstance(result, CostResult)
        assert result.cost_usd == 0.0
        assert result.unpriced is True
        assert result.is_anthropic is True

    def test_free_non_anthropic_model_is_not_unpriced(self) -> None:
        result = calculate_cost_result(
            "ollama/qwen3-embedding", tokens_input=_M, tokens_output=0
        )
        assert result.cost_usd == 0.0
        assert result.unpriced is False
        assert result.is_anthropic is False

    def test_priced_anthropic_model_is_not_unpriced(self) -> None:
        result = calculate_cost_result(
            "claude-sonnet-5", tokens_input=_M, tokens_output=0
        )
        assert result.cost_usd > 0.0
        assert result.unpriced is False

    def test_priced_non_anthropic_grok_is_not_unpriced(self) -> None:
        result = calculate_cost_result(
            "grok-build-0.1", tokens_input=_M, tokens_output=0
        )
        assert result.cost_usd > 0.0
        assert result.unpriced is False
        assert result.is_anthropic is False

    def test_priced_non_anthropic_codex_is_not_unpriced(self) -> None:
        result = calculate_cost_result(
            "gpt-5.3-codex", tokens_input=_M, tokens_output=0
        )
        assert result.cost_usd > 0.0
        assert result.unpriced is False
        assert result.is_anthropic is False

    def test_calculate_cost_matches_structured_cost_usd(self) -> None:
        model = "claude-opus-4-6"
        assert (
            calculate_cost(model, tokens_input=_M, tokens_output=_M)
            == calculate_cost_result(model, tokens_input=_M, tokens_output=_M).cost_usd
        )

    def test_empty_model_is_not_unpriced(self) -> None:
        # An empty model name is a caller bug, not an unpriced-Anthropic miss.
        result = calculate_cost_result("", tokens_input=_M, tokens_output=0)
        assert result.cost_usd == 0.0
        assert result.unpriced is False


# ---------------------------------------------------------------------------
# Sonnet-5 promo date gate — promo on/ before 2026-08-31, list rate after.
# ---------------------------------------------------------------------------


def test_sonnet5_promo_active_on_or_before_2026_08_31(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _D:
        @staticmethod
        def today() -> date:
            return date(2026, 8, 31)

    monkeypatch.setattr(p, "date", _D)
    assert p._lookup_prices("claude-sonnet-5") == (
        _SONNET5_INPUT,
        _SONNET5_OUTPUT,
        _SONNET5_CACHE_READ,
        _SONNET5_CACHE_WRITE,
    )


def test_sonnet5_reverts_to_list_rate_after_2026_08_31(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _D:
        @staticmethod
        def today() -> date:
            return date(2026, 9, 1)

    monkeypatch.setattr(p, "date", _D)
    assert p._lookup_prices("claude-sonnet-5") == (
        _SONNET_INPUT,
        _SONNET_OUTPUT,
        _SONNET_CACHE_READ,
        _SONNET_CACHE_WRITE,
    )


# ---------------------------------------------------------------------------
# input_price_per_million — the cost-tiered complexity-override comparator
# ---------------------------------------------------------------------------


class TestInputPricePerMillion:
    """The downgrade-only comparator for complexity overrides (no explicit
    tier ordering exists in the model catalog, so the input rate stands in
    for "which tier is costlier")."""

    def test_orders_haiku_below_sonnet_below_opus(self) -> None:
        assert (
            input_price_per_million("haiku")
            < input_price_per_million("sonnet")
            < input_price_per_million("opus")
        )

    def test_matches_pricing_table_value(self) -> None:
        assert input_price_per_million("haiku") == _HAIKU_INPUT
        assert input_price_per_million("sonnet") == _SONNET_INPUT
        assert input_price_per_million("opus") == _OPUS_INPUT

    def test_grok_priced_below_sonnet(self) -> None:
        """Grok legitimately downgrades-from sonnet under this comparator."""
        assert input_price_per_million("grok-build-0.1") < input_price_per_million(
            "sonnet"
        )

    def test_unpriced_non_anthropic_model_is_free_tier(self) -> None:
        """A self-hosted / Ollama Cloud model has no per-token rate — treated
        as the cheapest possible tier, so it can never be rejected as
        "costlier" by the downgrade-only policy."""
        assert input_price_per_million("glm-5.2:cloud") == 0.0
        assert input_price_per_million("my-custom-self-hosted-model:7b") == 0.0

    def test_empty_model_returns_zero(self) -> None:
        assert input_price_per_million("") == 0.0

    def test_case_insensitive(self) -> None:
        assert input_price_per_million("HAIKU") == input_price_per_million("haiku")
