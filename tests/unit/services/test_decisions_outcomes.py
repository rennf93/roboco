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


def test_parking_and_triage_and_review_golds() -> None:
    assert gold_for("parking", "limit_lifted_quickly") == {
        "gate": {"retry_soon": 1.0}
    }
    assert gold_for("parking", "limit_lifted_after_cooldown") == {
        "gate": {"park_standard": 1.0}
    }
    assert gold_for("parking", "re_limited_on_resume") == {
        "gate": {"escalate": 1.0}
    }
    assert gold_for("triage_failure", "flake_confirmed_by_later_failures") == {
        "gate": {"flaky": 1.0}
    }
    assert gold_for(
        "triage_failure", "regression_confirmed_no_recurrence"
    ) == {"gate": {"my_regression": 1.0}}
    # second_review: clean delivery proves NOT high-stakes; a post-gate
    # bounce proves it was.
    assert gold_for("second_review_eligibility", "qa_passed_after") == {
        "gate": {"true": 0.0, "false": 1.0}
    }
    assert gold_for("second_review_eligibility", "plan_bounced_after") == {
        "gate": {"true": 1.0, "false": 0.0}
    }
    # assembled_coherence: soft golds on its own legend.
    assert gold_for("assembled_coherence", "qa_passed_after") == {
        "gate": {"1": 0.5, "2": 0.5}
    }
    assert gold_for("assembled_coherence", "plan_bounced_after") == {
        "gate": {"0": 0.7, "1": 0.3}
    }


def test_parking_stale_is_unusable() -> None:
    assert ("parking", "stale_unresolved") in UNUSABLE_OUTCOMES
    assert gold_for("parking", "stale_unresolved") is None


def test_question_fate_gold_for_findings_mapping() -> None:
    from roboco.services.decisions.outcomes import question_fate_gold

    assert question_fate_gold("findings_mapping", "resolved") == {
        "true": 1.0,
        "false": 0.0,
    }
    assert question_fate_gold("findings_mapping", "re_raised") == {
        "true": 0.0,
        "false": 1.0,
    }
    # Waived says nothing about the diff: unusable per question.
    assert question_fate_gold("findings_mapping", "waived") is None
    assert question_fate_gold("some_other_pilot", "resolved") is None
