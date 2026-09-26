"""Tests for the decision outcome registry: the shared vocabulary between
outcome producers and the training exporter. A slug not in the registry is
never guessed; unusable slugs never yield gold."""

from roboco.services.decisions.outcomes import (
    UNUSABLE_OUTCOMES,
    example_gold,
    gold_for,
    usable_slugs,
    validate_gold_shape,
)


def test_known_outcomes_yield_gold() -> None:
    cleared = gold_for("self_heal", "cleared_after_gate")
    assert cleared == {"gate": {"true": 1.0, "false": 0.0}}
    failing = gold_for("self_heal", "still_failing_after_window")
    assert failing == {"gate": {"true": 0.0, "false": 1.0}}


def test_unknown_outcome_yields_no_gold() -> None:
    assert gold_for("self_heal", "some_future_slug") is None
    assert gold_for("some_other_pilot", "cleared_after_gate") is None


def test_unusable_outcome_yields_no_gold_even_if_registered() -> None:
    assert ("self_heal", "superseded_by_fix_task") in UNUSABLE_OUTCOMES
    assert gold_for("self_heal", "superseded_by_fix_task") is None


def test_usable_slugs_view_excludes_unusable() -> None:
    view = usable_slugs()
    assert ("self_heal", "cleared_after_gate") in view
    assert all(
        key not in UNUSABLE_OUTCOMES for key in view
    )


def test_example_gold_restricted_to_asked_questions() -> None:
    gold = {"gate": {"true": 1.0, "false": 0.0}, "phantom": {"true": 1.0}}
    questions = {"gate": {"type": "noul"}, "extra": {"type": "score"}}
    assert example_gold(gold, questions) == {"gate": {"true": 1.0, "false": 0.0}}


def test_example_gold_empty_when_no_overlap() -> None:
    gold = {"gate": {"true": 1.0}}
    assert example_gold(gold, {"other": {"type": "noul"}}) == {}


def test_gold_shape_noul() -> None:
    assert validate_gold_shape("noul", {"true": 1.0, "false": 0.0})
    # An extra key is not a noul outcome.
    assert not validate_gold_shape("noul", {"true": 1.0, "false": 0.0, "x": 0.0})


def test_gold_shape_score() -> None:
    assert validate_gold_shape("score", {"0": 0.5, "1": 0.5})
    # Score gold indexes the legend; named keys do not fit.
    assert not validate_gold_shape("score", {"true": 1.0})


def test_gold_shape_choice() -> None:
    assert validate_gold_shape("choice", {"park_standard": 1.0})
    assert validate_gold_shape("choice", {"a": 0.6, "b": 0.4})


def test_gold_shape_rejects_non_normalized_and_empty() -> None:
    assert not validate_gold_shape("noul", {})
    assert not validate_gold_shape("choice", {"a": 0.9})
