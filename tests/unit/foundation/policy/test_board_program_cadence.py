"""Pure cadence-slowing policy: sample/threshold boundaries, never accelerates."""

from __future__ import annotations

from roboco.foundation.policy.board_program_cadence import (
    CadenceLimits,
    SlowdownDecision,
    decide_slowdown,
    effective_interval_seconds,
)

MIN_SAMPLE = 5
THRESHOLD = 50.0
WEEK = 604800
HIGH_COST = 60.0


def _decide(
    *,
    cost: float | None = HIGH_COST,
    proposed: int = MIN_SAMPLE,
    approved: int = 0,
    cycles: int = 1,
    threshold: float = THRESHOLD,
) -> SlowdownDecision:
    return decide_slowdown(
        cost_per_cycle_usd=cost,
        items_proposed=proposed,
        items_approved=approved,
        cycles_counted=cycles,
        limits=CadenceLimits(MIN_SAMPLE, threshold),
    )


def test_below_minimum_sample_never_slowed() -> None:
    assert not _decide(proposed=MIN_SAMPLE - 1).slowed


def test_at_minimum_sample_slowed() -> None:
    assert _decide(proposed=MIN_SAMPLE).slowed


def test_threshold_boundary_is_inclusive() -> None:
    assert _decide(cost=THRESHOLD).slowed
    assert not _decide(cost=THRESHOLD - 0.01).slowed
    assert not _decide(cost=None).slowed


def test_threshold_is_configurable() -> None:
    assert not _decide(threshold=HIGH_COST + 1).slowed
    assert _decide(threshold=10.0).slowed


def test_approval_rate_that_justifies_cost_is_not_slowed() -> None:
    # 2 cycles, 4 approved -> 2/cycle -> $30 per approved < $50
    decision = _decide(approved=4, cycles=2)
    assert not decision.slowed
    assert decision.cost_per_approved_usd == HIGH_COST / 2


def test_approval_that_does_not_justify_cost_is_slowed() -> None:
    # 1 approved per cycle -> $60 per approved >= $50
    decision = _decide(approved=1)
    assert decision.slowed
    assert decision.cost_per_approved_usd == HIGH_COST


def test_slowdown_never_accelerates() -> None:
    assert effective_interval_seconds(WEEK, slowed=False, multiplier=3.0) == WEEK
    assert effective_interval_seconds(WEEK, slowed=True, multiplier=3.0) == WEEK * 3
    assert effective_interval_seconds(WEEK, slowed=True, multiplier=0.5) == WEEK
