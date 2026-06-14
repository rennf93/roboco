"""pass_review requires a verification for every acceptance criterion.

A gestalt "looks good" approval is how a silently-unbuilt criterion slips
through QA; the coverage gate forces one verdict per criterion before a pass is
allowed. If a criterion does not hold, QA fails the review instead.
"""

from __future__ import annotations

from types import SimpleNamespace

from roboco.services.gateway.choreographer import Choreographer


def _task(criteria: list[str]) -> SimpleNamespace:
    return SimpleNamespace(acceptance_criteria=criteria)


def test_no_criteria_imposes_no_requirement() -> None:
    assert Choreographer._qa_ac_coverage_check(_task([]), None) is None


def test_full_coverage_passes() -> None:
    t = _task(["returns 200", "includes timestamp"])
    assert (
        Choreographer._qa_ac_coverage_check(t, ["200 ok via test", "ts in diff"])
        is None
    )


def test_partial_coverage_is_rejected() -> None:
    t = _task(["a", "b", "c"])
    env = Choreographer._qa_ac_coverage_check(t, ["only one verified"])
    assert env is not None
    body = env.as_dict()
    assert body["error"] == "invalid_state", body
    assert "fail_review" in (env.remediate or "")


def test_missing_verdicts_entirely_is_rejected() -> None:
    t = _task(["a", "b"])
    env = Choreographer._qa_ac_coverage_check(t, None)
    assert env is not None


def test_blank_verdicts_do_not_count() -> None:
    t = _task(["a", "b"])
    env = Choreographer._qa_ac_coverage_check(t, ["a ok", "   "])
    assert env is not None, "whitespace-only verdict must not satisfy a criterion"


def test_extra_verdicts_are_allowed() -> None:
    t = _task(["a"])
    assert Choreographer._qa_ac_coverage_check(t, ["a ok", "bonus note"]) is None


def test_verdicts_fold_into_persisted_notes() -> None:
    merged = Choreographer._merge_ac_verdicts_into_notes(
        "base review", ["a ok", "b ok"]
    )
    assert "Per-criterion verification" in merged
    assert "- a ok" in merged
    assert "- b ok" in merged


def test_merge_with_no_verdicts_returns_notes_unchanged() -> None:
    assert Choreographer._merge_ac_verdicts_into_notes("base", None) == "base"
