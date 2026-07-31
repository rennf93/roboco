"""The escalate_up/unblock oscillation breaker.

Live wedge: a cell PM's escalate_up auto-blocks a task and the Main PM's
unblock restores it, repeat, forever — the per-(agent, task) respawn breaker
in the orchestrator misses this because the escalator and the resolver each
own only half the round trips, so neither individual counter accrues at the
cycle's real rate. ``unblock`` now stamps a task-scoped, progress-
discriminated strike counter (``markers.oscillation_strikes``) on every
restore; past the trip threshold the task is force-blocked for a human
instead of restored again, and further ``unblock`` calls on it are refused
until an admin override clears it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.foundation.policy.content import markers
from roboco.models.base import BlockerResolverType, TaskStatus
from roboco.services.gateway.choreographer import Choreographer, ChoreographerDeps

# Mirrors _OSCILLATION_TRIP_THRESHOLD in _impl.py — ruff PLR2004 forbids
# magic-value comparisons.
_TRIP_THRESHOLD = 5


def _make_deps(**overrides: Any) -> ChoreographerDeps:
    base: dict[str, Any] = {
        "task": AsyncMock(),
        "work_session": AsyncMock(),
        "git": AsyncMock(),
        "a2a": AsyncMock(),
        "journal": AsyncMock(),
        "audit": AsyncMock(),
        "evidence_repo": AsyncMock(),
    }
    base.update(overrides)
    base["journal"].has_decision_for_task.return_value = True
    base["journal"].latest_decision_at.return_value = datetime.now(UTC)
    # _ensure_pm_decision's journal write is savepoint-guarded — an
    # unconfigured AsyncMock's begin_nested() call returns a raw unawaited
    # coroutine, which `async with` cannot use.
    base["task"].session.begin_nested = MagicMock(
        return_value=MagicMock(
            __aenter__=AsyncMock(return_value=None),
            __aexit__=AsyncMock(return_value=False),
        )
    )
    return ChoreographerDeps(**base)


def _osc_setup() -> tuple[Choreographer, Any, Any, Any, Any]:
    """A blocked task whose ``unblock_with_restore`` returns the SAME mock
    object each call (one real ORM row across requests), with commits/
    revision_count seeded so the progress fingerprint is well-formed.
    """
    pm_id = uuid4()
    task_id = uuid4()
    t = MagicMock(
        id=task_id,
        status="blocked",
        pre_block_state="in_progress",
        pre_block_assignee=uuid4(),
        pre_block_metadata={},
        dependency_ids=[],
        orchestration_markers=None,
        commits=[],
        revision_count=0,
        blocker_raised_by=uuid4(),
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.unblock_with_restore.return_value = t
    task_svc.unmet_dependency_ids.return_value = []
    # A PM coordination root never commits itself — held constant here so
    # existing scenarios (driven purely by commits/revision_count) are
    # unaffected; tests targeting the child-count component override it.
    task_svc.terminal_children_count = AsyncMock(return_value=0)
    c = Choreographer(_make_deps(task=task_svc))
    return c, pm_id, task_id, t, task_svc


async def _unblock_once(c: Choreographer, pm_id: Any, task_id: Any, t: Any) -> Any:
    """Re-block before each call — a fresh round trip in the cycle."""
    t.status = "blocked"
    return await c.unblock(pm_id, task_id, "resolved upstream; restoring")


@pytest.mark.asyncio
async def test_no_progress_trips_after_threshold_cycles() -> None:
    c, pm_id, task_id, t, task_svc = _osc_setup()
    cc: Any = c
    notify = AsyncMock()
    cc._notify_ceo_oscillation = notify

    envs = [
        await _unblock_once(c, pm_id, task_id, t) for _ in range(_TRIP_THRESHOLD + 1)
    ]

    for env in envs[:-1]:
        assert env.error is None, env.as_dict()
    tripped = envs[-1]
    assert tripped.error is None, tripped.as_dict()
    assert "force-blocked" in tripped.next
    assert markers.is_oscillation_tripped(t) is True
    assert markers.get_oscillation_strikes(t) == _TRIP_THRESHOLD + 1
    notify.assert_awaited_once()
    assert t.blocker_resolver_type == BlockerResolverType.HUMAN
    task_svc.admin_set_status.assert_awaited_once_with(
        task_id, TaskStatus.BLOCKED, actor_role="system"
    )


@pytest.mark.asyncio
async def test_tripped_task_refuses_further_unblock() -> None:
    c, pm_id, task_id, t, task_svc = _osc_setup()
    cc: Any = c
    cc._notify_ceo_oscillation = AsyncMock()
    markers.mark_oscillation_tripped(t)
    admin_calls_before = task_svc.admin_set_status.await_count

    env = await _unblock_once(c, pm_id, task_id, t)

    assert env.error == "invalid_state"
    assert "force-blocked" in env.message
    # The guard short-circuits before the restore path ever runs again.
    assert task_svc.admin_set_status.await_count == admin_calls_before
    task_svc.unblock_with_restore.assert_not_awaited()


@pytest.mark.asyncio
async def test_progress_between_rounds_prevents_trip() -> None:
    """A commit landing between escalations is real progress, not a loop —
    strikes reset every round, so the breaker never trips no matter how many
    rounds occur."""
    c, pm_id, task_id, t, _task_svc = _osc_setup()
    cc: Any = c
    cc._notify_ceo_oscillation = AsyncMock()

    for i in range(_TRIP_THRESHOLD + 3):
        # A fresh commit lands every round — the commit count strictly grows.
        t.commits = [{"sha": str(j)} for j in range(i + 1)]
        env = await _unblock_once(c, pm_id, task_id, t)
        assert env.error is None, env.as_dict()

    assert markers.is_oscillation_tripped(t) is False
    assert markers.get_oscillation_strikes(t) == 1
    cc._notify_ceo_oscillation.assert_not_awaited()


@pytest.mark.asyncio
async def test_revision_count_advance_also_counts_as_progress() -> None:
    c, pm_id, task_id, t, _task_svc = _osc_setup()
    cc: Any = c
    cc._notify_ceo_oscillation = AsyncMock()

    for _ in range(_TRIP_THRESHOLD):
        t.revision_count += 1  # a genuine revision round resolved
        env = await _unblock_once(c, pm_id, task_id, t)
        assert env.error is None, env.as_dict()

    assert markers.is_oscillation_tripped(t) is False
    cc._notify_ceo_oscillation.assert_not_awaited()


@pytest.mark.asyncio
async def test_static_terminal_children_still_trips() -> None:
    """A coordination root with existing terminal children that DON'T change
    between rounds still trips — the child count is one more progress
    signal, not blanket forgiveness for an otherwise stalled cycle."""
    c, pm_id, task_id, t, task_svc = _osc_setup()
    cc: Any = c
    cc._notify_ceo_oscillation = AsyncMock()
    task_svc.terminal_children_count = AsyncMock(return_value=3)

    envs = [
        await _unblock_once(c, pm_id, task_id, t) for _ in range(_TRIP_THRESHOLD + 1)
    ]

    tripped = envs[-1]
    assert tripped.error is None, tripped.as_dict()
    assert markers.is_oscillation_tripped(t) is True
    cc._notify_ceo_oscillation.assert_awaited_once()


@pytest.mark.asyncio
async def test_terminal_child_completing_between_rounds_resets_strikes() -> None:
    """On a PM coordination root the commit/revision_count components are
    structurally static (PMs never commit) — a child landing COMPLETED
    between escalations is the only progress signal available, and must
    still reset strikes like the other two."""
    c, pm_id, task_id, t, task_svc = _osc_setup()
    cc: Any = c
    cc._notify_ceo_oscillation = AsyncMock()

    counts = iter([0, 0, 1, 1, 2])  # a child completes on round 3, then round 5
    task_svc.terminal_children_count = AsyncMock(side_effect=lambda _tid: next(counts))

    for _ in range(5):
        env = await _unblock_once(c, pm_id, task_id, t)
        assert env.error is None, env.as_dict()

    assert markers.is_oscillation_tripped(t) is False
    # Round 5's fingerprint differs from round 4's (1 -> 2 children), so this
    # round's strike count reset to 1.
    assert markers.get_oscillation_strikes(t) == 1
    cc._notify_ceo_oscillation.assert_not_awaited()


@pytest.mark.asyncio
async def test_notify_named_with_strikes_and_both_agents() -> None:
    c, pm_id, task_id, t, _task_svc = _osc_setup()
    cc: Any = c
    notify = AsyncMock()
    cc._notify_ceo_oscillation = notify
    escalator_id = t.blocker_raised_by

    for _ in range(_TRIP_THRESHOLD + 1):
        await _unblock_once(c, pm_id, task_id, t)

    notify.assert_awaited_once_with(
        task_id,
        _TRIP_THRESHOLD + 1,
        t.title,
        escalator_id=escalator_id,
        resolver_id=pm_id,
    )
