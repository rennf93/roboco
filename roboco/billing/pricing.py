"""
Token pricing for Claude API models.

Implements per-model USD cost calculation based on Anthropic's published
pricing. All prices are in USD per 1 million tokens.

Unknown model names return 0.0 without raising so callers don't need to
guard against missing pricing data.  Self-hosted Ollama models always
return 0.0 (no API cost) — matched by the ``ollama/`` prefix convention.
"""

from __future__ import annotations

import structlog

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Per-model pricing table
# Format: model_name_fragment → (input_usd_per_1m, output_usd_per_1m,
#                                cache_read_usd_per_1m, cache_write_usd_per_1m)
#
# Cache read is charged at ~10 % of the input price.
# Cache write is charged at ~25 % of the input price.
#
# Match on *substring* of model name so "claude-opus-4-6" and "opus" both
# resolve to the same tier.
# ---------------------------------------------------------------------------
_PRICING: list[tuple[str, float, float, float, float]] = [
    # (fragment, input/1M, output/1M, cache_read/1M, cache_write/1M)
    # Opus 4 family
    ("claude-opus-4", 5.00, 25.00, 0.50, 6.25),
    # Sonnet 4 / 3.7 / 3.5 family
    ("claude-sonnet-4", 3.00, 15.00, 0.30, 0.75),
    ("claude-3-7-sonnet", 3.00, 15.00, 0.30, 0.75),
    ("claude-3-5-sonnet", 3.00, 15.00, 0.30, 0.75),
    # Haiku family
    ("claude-haiku-4", 1.00, 5.00, 0.10, 1.25),
    ("claude-haiku-3-5", 1.00, 5.00, 0.10, 1.25),
    ("claude-3-5-haiku", 1.00, 5.00, 0.10, 1.25),
    ("claude-haiku-3", 0.25, 1.25, 0.025, 0.0625),
    # Short aliases used in ROLE_MODEL_MAP / MODEL_MAP
    ("opus", 5.00, 25.00, 0.50, 6.25),
    ("sonnet", 3.00, 15.00, 0.30, 0.75),
    ("haiku", 1.00, 5.00, 0.10, 1.25),
]

_MILLION = 1_000_000.0


def _lookup_prices(lower: str) -> tuple[float, float, float, float] | None:
    """Return the (input, output, cache_read, cache_write) rates for a model.

    Matches ``lower`` (a lowercased model name) against the pricing table by
    substring, longest fragment wins. Returns None when no fragment matches.
    """
    best_fragment_len = 0
    best_prices: tuple[float, float, float, float] | None = None
    for fragment, inp_price, out_price, cr_price, cw_price in _PRICING:
        if fragment in lower and len(fragment) > best_fragment_len:
            best_fragment_len = len(fragment)
            best_prices = (inp_price, out_price, cr_price, cw_price)
    return best_prices


def calculate_cost(
    model: str,
    tokens_input: int,
    tokens_output: int,
    tokens_cache_read: int = 0,
    tokens_cache_write: int = 0,
) -> float:
    """Calculate the estimated USD cost for a model invocation.

    Matches the model name against the known pricing table using substring
    search (longest match wins).  Unknown models return 0.0 without raising.
    Self-hosted Ollama models (``ollama/`` prefix) always return 0.0.

    Args:
        model: Model name or short alias (e.g. ``"claude-sonnet-4-6"``,
               ``"sonnet"``, ``"opus"``).
        tokens_input: Number of input tokens (prompt / context).
        tokens_output: Number of output tokens (completion).
        tokens_cache_read: Prompt-cache read tokens (charged at reduced rate).
        tokens_cache_write: Prompt-cache write tokens (charged at reduced rate).

    Returns:
        Estimated cost in USD as a float.  Returns 0.0 for unknown models
        rather than raising.
    """
    if not model:
        return 0.0

    lower = model.lower()

    # Self-hosted Ollama models have no API cost.
    if lower.startswith("ollama/"):
        return 0.0

    # Find the best (longest fragment) match
    best_prices = _lookup_prices(lower)
    if best_prices is None:
        logger.warning("No pricing data found for model", model=model)
        return 0.0

    inp_price, out_price, cr_price, cw_price = best_prices

    cost = (
        tokens_input * inp_price / _MILLION
        + tokens_output * out_price / _MILLION
        + tokens_cache_read * cr_price / _MILLION
        + tokens_cache_write * cw_price / _MILLION
    )
    return round(cost, 8)
