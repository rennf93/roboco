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
    CATCH_GATE_CONVENTIONS,
    CATCH_GATE_PR,
    CATCH_GATE_QA,
    CatchVerdict,
    CohortResult,
    DeterministicMetrics,
    FixtureResult,
    JudgeVerdict,
    OrchestratorStageSpawner,
    _build_judge_prompt,
    _score_catch,
)
from roboco.runtime.orchestrator import AgentOrchestrator

_EXPECTED_TOTAL_TOKENS = 180
_HALF_PASS_RATE = 0.5
_COHORT_TOTAL_TOKENS = 600
_COHORT_MEAN_CYCLE_SECONDS = 20.0
_COHORT_MEAN_JUDGE_SCORE = 5.0
_PASSING_JUDGE_SCORE = 4
_DEFAULT_STAGE_TIMEOUT_SECONDS = 900.0


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


def test_score_catch_returns_none_for_a_fixture_with_no_expected_gate() -> None:
    """A pre-existing golden fixture (no expected_catch_gate) never enters
    the catch-rate — the fixture-level verdict must be None, not a miss."""
    assert _score_catch(None, frozenset(), frozenset()) is None
    assert _score_catch("", frozenset({"qa"}), frozenset()) is None


def test_score_catch_qa_gate_matches_only_a_qa_origin_finding() -> None:
    caught = _score_catch(CATCH_GATE_QA, frozenset({"qa"}), frozenset())
    assert caught is not None
    assert caught.caught is True
    assert caught.evidence == ["finding_origin:qa"]

    missed = _score_catch(CATCH_GATE_QA, frozenset({"pr_gate"}), frozenset())
    assert missed is not None
    assert missed.caught is False
    assert missed.evidence == []


def test_score_catch_qa_gate_matches_a_qa_fail_bounce_event_too() -> None:
    verdict = _score_catch(CATCH_GATE_QA, frozenset(), frozenset({"task.qa_fail"}))
    assert verdict is not None
    assert verdict.caught is True
    assert verdict.evidence == ["audit_event:task.qa_fail"]


def test_score_catch_pr_gate_is_not_satisfied_by_a_bare_qa_finding() -> None:
    """Unlike conventions_check, the pr_gate expected-catch-gate requires a
    pr_gate-origin signal — a QA finding alone is not the PR gate firing."""
    verdict = _score_catch(CATCH_GATE_PR, frozenset({"qa"}), frozenset())
    assert verdict is not None
    assert verdict.caught is False


def test_score_catch_conventions_check_accepts_either_qa_or_pr_gate_origin() -> None:
    via_qa = _score_catch(CATCH_GATE_CONVENTIONS, frozenset({"qa"}), frozenset())
    via_pr_gate = _score_catch(
        CATCH_GATE_CONVENTIONS, frozenset({"pr_gate"}), frozenset()
    )
    assert via_qa is not None and via_qa.caught is True
    assert via_pr_gate is not None and via_pr_gate.caught is True


def test_score_catch_unrecognized_gate_falls_back_to_any_signal() -> None:
    """A gate name the sibling fixture leaf coined that this module's
    vocabulary doesn't (yet) recognize must not silently mis-score every
    such fixture a miss — it degrades to "did anything fire"."""
    verdict = _score_catch("some-future-gate-name", frozenset({"pm"}), frozenset())
    assert verdict is not None
    assert verdict.caught is True
    assert verdict.evidence == ["finding_origin:pm"]


def _fixture_result_with_catch(catch: CatchVerdict | None) -> FixtureResult:
    return FixtureResult(
        fixture_key="seeded",
        metrics=_metrics(),
        judge=JudgeVerdict(score=None, rationale=None),
        catch=catch,
    )


def test_cohort_catch_rate_excludes_fixtures_with_no_catch_verdict() -> None:
    fixtures = [
        _fixture_result_with_catch(None),  # a pre-existing golden fixture
        _fixture_result_with_catch(
            CatchVerdict(expected_gate=CATCH_GATE_QA, caught=True, evidence=["x"])
        ),
        _fixture_result_with_catch(
            CatchVerdict(expected_gate=CATCH_GATE_PR, caught=False, evidence=[])
        ),
    ]
    cohort = CohortResult(role_slug="be-qa", cohort_name="x", fixtures=fixtures)

    caught, seeded = cohort.catch_rate_stats
    assert (caught, seeded) == (1, 2)
    assert cohort.catch_rate == pytest.approx(0.5)


def test_cohort_catch_rate_is_none_when_no_fixture_was_seeded() -> None:
    cohort = CohortResult(
        role_slug="be-dev-1",
        cohort_name="x",
        fixtures=[_fixture_result_with_catch(None)],
    )
    assert cohort.catch_rate_stats == (0, 0)
    assert cohort.catch_rate is None


def test_cohort_as_dict_carries_catch_rate_as_a_sibling_of_judge_unchanged() -> None:
    """catch_rate is a NEW sibling field — the pre-existing judge object's
    shape must stay byte-for-byte identical, and catch_rate must never be
    nested inside it."""
    fixtures = [
        _fixture_result_with_catch(
            CatchVerdict(
                expected_gate=CATCH_GATE_QA, caught=True, evidence=["finding_origin:qa"]
            )
        ),
    ]
    cohort = CohortResult(role_slug="be-qa", cohort_name="x", fixtures=fixtures)
    payload = cohort.as_dict()

    assert payload["judge"] == {"mean_score": None, "non_deterministic": True}
    assert payload["catch_rate"] == {"caught": 1, "seeded": 1, "rate": 1.0}
    assert "catch_rate" not in payload["judge"]
    assert payload["fixtures"][0]["catch"] == {
        "expected_gate": CATCH_GATE_QA,
        "caught": True,
        "evidence": ["finding_origin:qa"],
    }


def test_cohort_as_dict_reports_null_catch_for_a_fixture_with_no_verdict() -> None:
    cohort = CohortResult(
        role_slug="be-dev-1",
        cohort_name="x",
        fixtures=[_fixture_result_with_catch(None)],
    )
    payload = cohort.as_dict()
    assert payload["catch_rate"] == {"caught": 0, "seeded": 0, "rate": None}
    assert payload["fixtures"][0]["catch"] is None


def test_orchestrator_stage_spawner_constructs_real_orchestrator() -> None:
    """The real-spawn path is wired: OrchestratorStageSpawner() constructs
    without raising, holds a real AgentOrchestrator (built the same way the
    production dispatcher builds one), and defaults its stage timeout to
    900.0 seconds. The isolation boundary is the disposable orchestrator
    URL + throwaway DB wired in _bench_environment, not the spawner itself."""
    spawner = OrchestratorStageSpawner()

    assert isinstance(spawner._orchestrator, AgentOrchestrator)
    assert spawner._stage_timeout_seconds == _DEFAULT_STAGE_TIMEOUT_SECONDS
