"""Unit tests for the eval bench's scorer math (roboco/eval/runner.py).

Pure dataclass/aggregate-property tests — no DB, no network, no asyncio.
`_build_judge_prompt` and `BenchJudge`'s score-parsing regex are covered too
since both are pure string logic with no I/O.
"""

from __future__ import annotations

import pytest
from roboco.eval.fixtures import FIXTURES
from roboco.eval.runner import (
    _JUDGE_SCORE_RE,
    CohortResult,
    DeterministicMetrics,
    FixtureResult,
    JudgeVerdict,
    OrchestratorStageSpawner,
    _build_judge_prompt,
)

_EXPECTED_TOTAL_TOKENS = 180
_HALF_PASS_RATE = 0.5
_COHORT_TOTAL_TOKENS = 600
_COHORT_MEAN_CYCLE_SECONDS = 20.0
_COHORT_MEAN_JUDGE_SCORE = 5.0
_PASSING_JUDGE_SCORE = 4


def _metrics(
    *,
    final_status: str = "completed",
    stalled: bool = False,
    cycle_time_seconds: float = 10.0,
    tokens_input: int = 100,
    tokens_output: int = 50,
    tokens_cache_read: int = 0,
    tokens_cache_write: int = 0,
    estimated_cost_usd: float = 0.01,
) -> DeterministicMetrics:
    return DeterministicMetrics(
        final_status=final_status,
        stalled=stalled,
        revision_count=0,
        cycle_time_seconds=cycle_time_seconds,
        tokens_input=tokens_input,
        tokens_output=tokens_output,
        tokens_cache_read=tokens_cache_read,
        tokens_cache_write=tokens_cache_write,
        estimated_cost_usd=estimated_cost_usd,
    )


def test_deterministic_metrics_total_tokens_sums_all_four_buckets() -> None:
    m = _metrics(
        tokens_input=100, tokens_output=50, tokens_cache_read=25, tokens_cache_write=5
    )
    assert m.total_tokens == _EXPECTED_TOTAL_TOKENS


def test_fixture_result_passed_requires_completed_and_not_stalled() -> None:
    passed = FixtureResult(
        fixture_key="a",
        metrics=_metrics(final_status="completed", stalled=False),
        judge=JudgeVerdict(score=None, rationale=None),
    )
    assert passed.passed is True

    cancelled = FixtureResult(
        fixture_key="b",
        metrics=_metrics(final_status="cancelled", stalled=False),
        judge=JudgeVerdict(score=None, rationale=None),
    )
    assert cancelled.passed is False

    # A stall that happens to leave the row at "completed" is still not a
    # pass — `stalled` overrides the status.
    stalled_completed = FixtureResult(
        fixture_key="c",
        metrics=_metrics(final_status="completed", stalled=True),
        judge=JudgeVerdict(score=None, rationale=None),
    )
    assert stalled_completed.passed is False


def _sample_cohort() -> CohortResult:
    fixtures = [
        FixtureResult(
            fixture_key="a",
            metrics=_metrics(
                final_status="completed",
                cycle_time_seconds=10.0,
                estimated_cost_usd=0.10,
                tokens_input=100,
                tokens_output=100,
            ),
            judge=JudgeVerdict(score=5, rationale="great"),
        ),
        FixtureResult(
            fixture_key="b",
            metrics=_metrics(
                final_status="needs_revision",
                stalled=True,
                cycle_time_seconds=30.0,
                estimated_cost_usd=0.20,
                tokens_input=200,
                tokens_output=200,
            ),
            judge=JudgeVerdict(score=None, rationale="judge unavailable"),
        ),
    ]
    return CohortResult(role_slug="be-dev-1", cohort_name="baseline", fixtures=fixtures)


def test_cohort_pass_rate_and_totals() -> None:
    cohort = _sample_cohort()

    assert cohort.pass_rate == _HALF_PASS_RATE
    assert cohort.total_cost_usd == pytest.approx(0.3)
    assert cohort.total_tokens == _COHORT_TOTAL_TOKENS
    assert cohort.mean_cycle_time_seconds == _COHORT_MEAN_CYCLE_SECONDS
    # Only fixture "a" has a judge score; "b"'s None is excluded from the mean.
    assert cohort.mean_judge_score == _COHORT_MEAN_JUDGE_SCORE


def test_cohort_mean_judge_score_is_none_when_no_fixture_was_scored() -> None:
    fixtures = [
        FixtureResult(
            fixture_key="a",
            metrics=_metrics(),
            judge=JudgeVerdict(score=None, rationale="judge unavailable"),
        )
    ]
    cohort = CohortResult(role_slug="be-dev-1", cohort_name="x", fixtures=fixtures)
    assert cohort.mean_judge_score is None


def test_cohort_with_no_fixtures_is_a_zero_result_not_a_crash() -> None:
    cohort = CohortResult(role_slug="be-dev-1", cohort_name="x", fixtures=[])
    assert cohort.pass_rate == 0.0
    assert cohort.total_cost_usd == 0.0
    assert cohort.total_tokens == 0
    assert cohort.mean_cycle_time_seconds == 0.0
    assert cohort.mean_judge_score is None


def test_cohort_as_dict_round_trips_every_fixture() -> None:
    fixtures = [
        FixtureResult(
            fixture_key="a",
            metrics=_metrics(),
            judge=JudgeVerdict(_PASSING_JUDGE_SCORE, "solid"),
        ),
    ]
    cohort = CohortResult(role_slug="be-dev-1", cohort_name="x", fixtures=fixtures)
    payload = cohort.as_dict()

    assert payload["role_slug"] == "be-dev-1"
    assert payload["cohort_name"] == "x"
    assert payload["aggregate"]["fixture_count"] == 1
    assert payload["aggregate"]["pass_rate"] == 1.0
    # Judge fields live under their own nested, explicitly-marked object —
    # never flat beside deterministic metrics — so a naive diff can't read
    # judge noise as a regression.
    assert "mean_judge_score" not in payload["aggregate"]
    assert payload["judge"] == {
        "mean_score": _PASSING_JUDGE_SCORE,
        "non_deterministic": True,
    }
    assert len(payload["fixtures"]) == 1
    assert payload["fixtures"][0]["fixture_key"] == "a"
    assert "judge_score" not in payload["fixtures"][0]
    assert payload["fixtures"][0]["judge"] == {
        "score": _PASSING_JUDGE_SCORE,
        "rationale": "solid",
        "non_deterministic": True,
    }


def test_judge_score_regex_parses_the_required_reply_shape() -> None:
    reply = "Score: 4\nRationale: matches the expectation closely.\n"
    match = _JUDGE_SCORE_RE.search(reply)
    assert match is not None
    assert int(match.group(1)) == _PASSING_JUDGE_SCORE


def test_judge_score_regex_is_case_insensitive_and_tolerates_spacing() -> None:
    assert _JUDGE_SCORE_RE.search("score:5") is not None
    assert _JUDGE_SCORE_RE.search("SCORE :   3") is not None


def test_judge_score_regex_rejects_out_of_range_scores() -> None:
    assert _JUDGE_SCORE_RE.search("Score: 0") is None
    assert _JUDGE_SCORE_RE.search("Score: 6") is None


def test_build_judge_prompt_includes_the_expectation_and_acceptance_criteria() -> None:
    fixture = FIXTURES[0]
    prompt = _build_judge_prompt(fixture, diff="+ fixed line", notes="dev notes here")

    assert fixture.title in prompt
    assert fixture.expectations in prompt
    for criterion in fixture.acceptance_criteria:
        assert criterion in prompt
    assert "+ fixed line" in prompt
    assert "dev notes here" in prompt


def test_build_judge_prompt_handles_empty_diff_and_notes() -> None:
    fixture = FIXTURES[0]
    prompt = _build_judge_prompt(fixture, diff="", notes="")
    assert "(empty diff)" in prompt
    assert "(no notes)" in prompt


def test_orchestrator_stage_spawner_is_cut_and_refuses_to_construct() -> None:
    """The real-spawn path is deliberately disabled this release (its MCP
    wiring would authenticate against the REAL production orchestrator) —
    this is the one runnable check that the cut stays in place."""
    with pytest.raises(NotImplementedError, match="cut from this release"):
        OrchestratorStageSpawner()
