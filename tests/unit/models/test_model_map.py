"""MODEL_MAP resolves the short aliases to current, priced model ids."""

from __future__ import annotations

from roboco.billing.pricing import calculate_cost
from roboco.models.runtime import MODEL_MAP, ROLE_MODEL_MAP

_M = 1_000_000


def test_sonnet_alias_resolves_to_sonnet_5_5() -> None:
    assert MODEL_MAP["sonnet"] == "claude-sonnet-5-5"


def test_opus_alias_resolves_to_opus_5_5() -> None:
    # Opus 5.5 replaces Opus 5: newer and cheaper ($4/$20 vs $5/$25).
    assert MODEL_MAP["opus"] == "claude-opus-5-5"


def test_haiku_alias_resolves_to_haiku_5_5() -> None:
    assert MODEL_MAP["haiku"] == "claude-haiku-5-5"


def test_sonnet_is_priced() -> None:
    # Guard against pointing an alias at an unpriced model (silent $0 cost-count).
    cost = calculate_cost(MODEL_MAP["sonnet"], tokens_input=_M, tokens_output=0)
    assert cost > 0.0


def test_opus_is_priced() -> None:
    # Same silent-$0 guard for the opus alias.
    cost = calculate_cost(MODEL_MAP["opus"], tokens_input=_M, tokens_output=0)
    assert cost > 0.0


def test_haiku_is_priced() -> None:
    # Same silent-$0 guard for the haiku alias.
    cost = calculate_cost(MODEL_MAP["haiku"], tokens_input=_M, tokens_output=0)
    assert cost > 0.0


def test_no_lifecycle_role_routes_below_the_structured_verb_floor() -> None:
    # Retired haiku from lifecycle roles (2026-07-24): a haiku QA/documenter
    # can't emit the structured review envelopes (criteria_verified, findings)
    # and loops without passing. No role's default may be a haiku-class tier.
    for role, model in ROLE_MODEL_MAP.items():
        assert "haiku" not in model.lower(), f"{role} routes to a below-floor {model}"


def test_main_pm_role_routes_to_sonnet() -> None:
    # Phase 2 experiment: main_pm off Opus (Sonnet's cache read/write is half Opus's).
    assert ROLE_MODEL_MAP["main_pm"] == "sonnet"
