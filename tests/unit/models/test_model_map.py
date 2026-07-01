"""MODEL_MAP resolves the short aliases to current, priced model ids."""

from __future__ import annotations

from roboco.billing.pricing import calculate_cost
from roboco.models.runtime import MODEL_MAP

_M = 1_000_000


def test_sonnet_alias_resolves_to_sonnet_5() -> None:
    assert MODEL_MAP["sonnet"] == "claude-sonnet-5"


def test_opus_alias_unchanged() -> None:
    # CEO preference: stay on Opus 4.6 (4.7/4.8 not preferred).
    assert MODEL_MAP["opus"] == "claude-opus-4-6"


def test_sonnet_5_is_priced() -> None:
    # Guard against pointing an alias at an unpriced model (silent $0 cost-count).
    cost = calculate_cost(MODEL_MAP["sonnet"], tokens_input=_M, tokens_output=0)
    assert cost > 0.0
