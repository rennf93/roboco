"""The delegate sibling-dedup guard is serialized by a PostgreSQL
transaction-scoped advisory lock keyed by the parent task id, acquired at the
TOP of the delegate body (before the first ``get_subtasks`` read) and held
through ``create_subtask``'s flush + the outer request commit. Different
parents hash to different keys (seed ``1``, disjoint from the per-agent claim
lock's seed ``0``) so cross-parent delegates are not serialized.

CRITICAL regression guard: the lock is per-PARENT, not per-agent. A per-agent
lock would serialize all of a coordinator PM's delegates and regress
coordinator concurrency; the dedup invariant is per-parent, so only same-parent
delegates serialize.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.services.gateway.choreographer import (
    Choreographer,
    ChoreographerDeps,
    DelegateInputs,
)


def _make_deps(task: AsyncMock) -> ChoreographerDeps:
    base: dict[str, Any] = {
        "task": task,
        "work_session": AsyncMock(),
        "git": AsyncMock(),
        "a2a": AsyncMock(),
        "journal": AsyncMock(),
        "audit": AsyncMock(),
        "evidence_repo": AsyncMock(),
    }
    repo = base["evidence_repo"]
    for m in (
        "list_unread_a2a",
        "list_unread_mentions",
        "list_pending_notifications",
        "task_metadata_gaps",
        "recent_team_activity",
        "blockers_in_lane",
        "journal_highlights_for_task",
    ):
        getattr(repo, m).return_value = []
    # A fresh decision within the recency window so the delegate tracing gate
    # (journal:decision required) passes without a separate write.
    base["journal"].latest_decision_at.return_value = datetime.now(UTC)
    return ChoreographerDeps(**base)


def _parent(pm_id: object) -> MagicMock:
    return MagicMock(
        id=uuid4(),
        project_id=uuid4(),
        product_id=None,
        status="in_progress",
        assigned_to=pm_id,
        # delegate obligates the PM's quick_context resumption section.
        quick_context="Decomposition planned; cells implement their slice next.",
    )


def _inputs() -> DelegateInputs:
    return DelegateInputs(
        title="Implement endpoint",
        description="Add /v1/foo endpoint with tests",
        assigned_to="be-dev-1",
        team="backend",
        task_type="code",
        nature="technical",
        acceptance_criteria=["GET /v1/foo returns 200 with body"],
        intends_to_touch=["backend/api/routers/foo.py"],
    )


@pytest.mark.asyncio
async def test_delegate_acquires_parent_lock_before_sibling_read() -> None:
    """The per-parent advisory lock MUST be acquired before the first
    ``get_subtasks`` read (the briefing's context read, which precedes the
    dedup guard's sibling read) and held through ``create_subtask``. This is
    the ordering that closes the TOCTOU: the second concurrent same-parent
    delegate blocks on the lock before it can read siblings, so its dedup read
    sees the first's committed subtask and is rejected. A lock acquired AFTER
    the dedup read but before the create would NOT close the race (the read
    already missed the concurrent insert) — so 'lock before create' alone is
    insufficient; the lock must precede the read."""
    pm_id = uuid4()
    parent = _parent(pm_id)
    task_svc = AsyncMock()
    task_svc.get.return_value = parent
    task_svc.agent_for.return_value = MagicMock(role="cell_pm", team="backend")
    task_svc.create_subtask.return_value = MagicMock(id=uuid4())

    # Shared call-order recorder: the lock must precede every get_subtasks
    # read (briefing context + dedup siblings) and the create.
    calls: list[str] = []

    async def _lock(_pid: object) -> None:
        calls.append("lock")

    async def _read_subtasks(_pid: object) -> list[Any]:
        calls.append("get_subtasks")
        return []

    async def _create_subtask(_req: object) -> Any:
        calls.append("create")
        return MagicMock(id=uuid4())

    task_svc.acquire_delegate_parent_lock = _lock
    task_svc.get_subtasks.side_effect = _read_subtasks
    task_svc.create_subtask.side_effect = _create_subtask

    deps = _make_deps(task_svc)
    c = Choreographer(deps)

    env = await c.delegate(pm_id, parent.id, _inputs())
    assert env.error is None, env.as_dict()

    # The flow reached the create (otherwise the lock-ordering assertion would
    # pass for the wrong reason — a short-circuit before the create).
    assert "create" in calls, calls

    # The lock was acquired exactly once, before the first sibling read, and
    # before the create — so it spans the dedup read -> create critical section.
    assert calls.count("lock") == 1, calls
    first_lock = calls.index("lock")
    first_read = calls.index("get_subtasks")
    first_create = calls.index("create")
    assert first_lock < first_read, (
        f"parent lock must be acquired before the first get_subtasks read; "
        f"order was {calls}"
    )
    assert first_lock < first_create, (
        f"parent lock must be held through create_subtask; order was {calls}"
    )


@pytest.mark.asyncio
async def test_delegate_still_creates_subtask_with_parent_lock() -> None:
    """No-regression: acquiring the per-parent lock must not break the normal
    delegate path — the subtask is still created (env.error is None,
    create_subtask awaited once). The lock is transparent to the happy path."""
    pm_id = uuid4()
    parent = _parent(pm_id)
    task_svc = AsyncMock()
    task_svc.get.return_value = parent
    task_svc.agent_for.return_value = MagicMock(role="cell_pm", team="backend")
    task_svc.get_subtasks.return_value = []
    task_svc.create_subtask.return_value = MagicMock(id=uuid4())
    # Leave the default AsyncMock for acquire_delegate_parent_lock so we can
    # assert it was awaited with the parent id (the lock is transparent to the
    # happy path — the create still runs).
    deps = _make_deps(task_svc)
    c = Choreographer(deps)

    env = await c.delegate(pm_id, parent.id, _inputs())
    assert env.error is None, env.as_dict()
    task_svc.create_subtask.assert_awaited_once()
    task_svc.acquire_delegate_parent_lock.assert_awaited_once_with(parent.id)
