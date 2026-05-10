"""Tier 1 — agent_loop budgets + verb retry limits."""

from __future__ import annotations

from roboco.foundation.policy import agent_loop

# Canonical defaults the foundation guarantees. Asserting against named
# constants keeps the contract explicit (and keeps PLR2004 happy — these
# are not magic numbers, they are the public API surface).
EXPECTED_TOOL_CALL_WARN_AT = 50
EXPECTED_TOOL_CALL_HALT_AT = 150
EXPECTED_LOOP_THRESHOLD = 3
EXPECTED_LOOP_WINDOW = 10
EXPECTED_PM_RESPAWN_MAX_UNPRODUCTIVE = 3
EXPECTED_VERB_RETRY_MAX_PER_MINUTE = 3
EXPECTED_HANDOFF_VERB_CAP = 3


def test_default_budget_has_canonical_thresholds() -> None:
    b = agent_loop.DEFAULT_BUDGET
    assert b.tool_call_warn_at == EXPECTED_TOOL_CALL_WARN_AT
    assert b.tool_call_halt_at == EXPECTED_TOOL_CALL_HALT_AT
    assert b.loop_threshold == EXPECTED_LOOP_THRESHOLD
    assert b.loop_window == EXPECTED_LOOP_WINDOW
    assert b.loop_action == "halt"  # NEW: was "warn-only"
    assert b.pm_respawn_max_unproductive == EXPECTED_PM_RESPAWN_MAX_UNPRODUCTIVE
    assert b.verb_retry_max_per_minute == EXPECTED_VERB_RETRY_MAX_PER_MINUTE


def test_warn_threshold_below_halt_threshold() -> None:
    assert (
        agent_loop.DEFAULT_BUDGET.tool_call_warn_at
        < agent_loop.DEFAULT_BUDGET.tool_call_halt_at
    )


def test_loop_threshold_below_loop_window() -> None:
    """Loop detection requires N repeats in a window of M; N must be < M."""
    assert (
        agent_loop.DEFAULT_BUDGET.loop_threshold < agent_loop.DEFAULT_BUDGET.loop_window
    )


def test_verb_retry_limits_cover_critical_handoff_verbs() -> None:
    """The verbs that surfaced in the 2026-05-10 retry storm are capped."""
    assert agent_loop.VERB_RETRY_LIMITS["i_am_done"] == EXPECTED_HANDOFF_VERB_CAP
    assert agent_loop.VERB_RETRY_LIMITS["complete"] == EXPECTED_HANDOFF_VERB_CAP
    assert agent_loop.VERB_RETRY_LIMITS["submit_up"] == EXPECTED_HANDOFF_VERB_CAP


def test_unlimited_retry_verbs_includes_discovery_verbs() -> None:
    """give_me_work / triage / evidence aren't subject to circuit breaker."""
    assert "give_me_work" in agent_loop.UNLIMITED_RETRY_VERBS
    assert "triage" in agent_loop.UNLIMITED_RETRY_VERBS
    assert "evidence" in agent_loop.UNLIMITED_RETRY_VERBS
    assert "i_am_idle" in agent_loop.UNLIMITED_RETRY_VERBS


def test_retry_limit_for_known_verb_returns_int() -> None:
    assert agent_loop.retry_limit_for("i_am_done") == EXPECTED_HANDOFF_VERB_CAP


def test_retry_limit_for_unlimited_verb_returns_none() -> None:
    assert agent_loop.retry_limit_for("give_me_work") is None


def test_retry_limit_for_unknown_verb_returns_default() -> None:
    """Unknown verbs fall through to the policy default."""
    assert (
        agent_loop.retry_limit_for("not_a_real_verb")
        == agent_loop.DEFAULT_BUDGET.verb_retry_max_per_minute
    )
