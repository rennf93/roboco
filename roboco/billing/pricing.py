"""
Token pricing for Claude API models.

Implements per-model USD cost calculation based on Anthropic's published
pricing. All prices are in USD per 1 million tokens.

Pricing is provider-aware. A model name resolves to one of four cases:

* **Anthropic** — priced from the table below by substring match.
* **Priced non-Anthropic** — xAI Grok (``grok-build-*``, billed per token via
  the xAI API) is priced from the table too. Match by substring like the rest.
* **Free non-Anthropic** — local self-hosted Ollama models (``ollama/`` prefix
  or bare model tags) and Ollama Cloud models (``:cloud`` tag). These have **no
  per-token cost**: local inference runs on owned hardware, and Ollama Cloud
  is billed by flat subscription / GPU-time rather than per token. Both return
  an intentional ``0.0`` — not an error condition, so they are not warned on.
* **Unpriced Anthropic** — a ``claude``-named model with no table entry (a new
  or renamed Claude model we have not priced yet). This is real spend we would
  otherwise undercount, so it logs a warning and returns ``0.0``.

Every path returns ``0.0`` rather than raising, so callers don't need to guard
against missing pricing data.
"""

from __future__ import annotations

from dataclasses import dataclass

import structlog

logger = structlog.get_logger(__name__)

# Substrings that identify an Anthropic (Claude) model. Used only to decide
# whether an *unpriced* model is a Claude model we forgot to price (warn) vs a
# non-Anthropic model that legitimately has no per-token cost (don't warn).
_ANTHROPIC_FRAGMENTS = ("claude", "opus", "sonnet", "haiku")

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
    # xAI Grok — priced non-Anthropic (per-token via the xAI API). Cached-input
    # read is $0.20/1M; xAI publishes no cache-write premium, so cache_write is
    # the normal input rate. https://docs.x.ai/developers/models
    ("grok-build", 1.00, 2.00, 0.20, 1.00),
    # Short aliases used in ROLE_MODEL_MAP / MODEL_MAP
    ("opus", 5.00, 25.00, 0.50, 6.25),
    ("sonnet", 3.00, 15.00, 0.30, 0.75),
    ("haiku", 1.00, 5.00, 0.10, 1.25),
]

_MILLION = 1_000_000.0


def _is_anthropic_model(lower: str) -> bool:
    """True if the lowercased model name looks like an Anthropic (Claude) model."""
    return any(fragment in lower for fragment in _ANTHROPIC_FRAGMENTS)


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


@dataclass(frozen=True)
class CostResult:
    """Estimated cost plus pricing attribution (#65).

    ``cost_usd`` is ``0.0`` for both a genuinely-free non-Anthropic model (local
    inference — no per-token cost) and an unpriced Anthropic model (a Claude
    model we forgot to price — real spend we are failing to count). ``unpriced``
    distinguishes them so a caller can surface the miss instead of silently
    reporting ``$0``. ``is_anthropic`` records which family the model resolved
    to. ``calculate_cost`` returns just the ``cost_usd`` float for existing
    callers; ``calculate_cost_result`` returns the full attribution.
    """

    cost_usd: float
    unpriced: bool
    is_anthropic: bool


def calculate_cost(
    model: str,
    tokens_input: int,
    tokens_output: int,
    tokens_cache_read: int = 0,
    tokens_cache_write: int = 0,
) -> float:
    """Calculate the estimated USD cost for a model invocation.

    Thin wrapper over :func:`calculate_cost_result` returning just the USD
    float (kept for existing callers). See :func:`calculate_cost_result` for the
    provider-aware miss handling and the ``unpriced`` attribution.
    """
    return calculate_cost_result(
        model,
        tokens_input=tokens_input,
        tokens_output=tokens_output,
        tokens_cache_read=tokens_cache_read,
        tokens_cache_write=tokens_cache_write,
    ).cost_usd


def calculate_cost_result(
    model: str,
    tokens_input: int,
    tokens_output: int,
    tokens_cache_read: int = 0,
    tokens_cache_write: int = 0,
) -> CostResult:
    """Calculate the estimated USD cost for a model invocation, with attribution.

    Matches the model name against the known pricing table using substring
    search (longest match wins). Provider-aware (see module docstring):
    non-Anthropic models (local Ollama, Ollama Cloud) have no per-token cost
    and return ``cost_usd=0.0, unpriced=False``; an unpriced Anthropic model
    returns ``cost_usd=0.0, unpriced=True`` and logs a warning since it
    represents real spend we are failing to count.

    Args:
        model: Model name or short alias (e.g. ``"claude-sonnet-4-6"``,
               ``"sonnet"``, ``"opus"``).
        tokens_input: Number of input tokens (prompt / context).
        tokens_output: Number of output tokens (completion).
        tokens_cache_read: Prompt-cache read tokens (charged at reduced rate).
        tokens_cache_write: Prompt-cache write tokens (charged at reduced rate).
            Reasoning/thinking tokens that a provider reports *separately* from
            output (e.g. xAI grok-build-*) are billed at the output rate by the
            caller folding them into ``tokens_output`` (see
            ``grok_cli_usage.usage_and_cost``).

    Returns:
        A :class:`CostResult` (``cost_usd``, ``unpriced``, ``is_anthropic``).
        ``cost_usd`` is ``0.0`` rather than raising for unpriced models.
    """
    if not model:
        # An empty model name is a caller bug, not an unpriced-Anthropic miss.
        return CostResult(cost_usd=0.0, unpriced=False, is_anthropic=False)

    lower = model.lower()
    is_anthropic = _is_anthropic_model(lower)

    best_prices = _lookup_prices(lower)
    if best_prices is None:
        # No per-token rate. Warn only for Anthropic models (real spend we are
        # undercounting); non-Anthropic models are local/subscription-billed
        # and have no per-token cost, so an intentional 0.0 is correct.
        if is_anthropic:
            logger.warning("No pricing data found for Anthropic model", model=model)
        else:
            logger.debug("Non-Anthropic model has no per-token cost", model=model)
        # Unpriced only when it is an Anthropic model we forgot to price; a
        # non-Anthropic model with no rate is intentionally free.
        return CostResult(
            cost_usd=0.0, unpriced=is_anthropic, is_anthropic=is_anthropic
        )

    inp_price, out_price, cr_price, cw_price = best_prices

    cost = (
        tokens_input * inp_price / _MILLION
        + tokens_output * out_price / _MILLION
        + tokens_cache_read * cr_price / _MILLION
        + tokens_cache_write * cw_price / _MILLION
    )
    return CostResult(
        cost_usd=round(cost, 8), unpriced=False, is_anthropic=is_anthropic
    )
