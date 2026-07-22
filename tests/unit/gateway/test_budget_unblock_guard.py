"""unblock's budget-breach re-check (ROBOCO_TASK_BUDGETS_ENABLED).

A task the orchestrator's budget sweep BLOCKed carries the BUDGET_BLOCKED
marker (`_handle_task_budget_breach`). `unblock` re-checks spend-vs-cap
before letting it through: still over refuses (naming the budget
remediation), so a PM can't silently re-breach the same cap the next tick;
under (the CEO raised the cap) clears the marker and the unblock proceeds
exactly as before.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.config import settings
from roboco.foundation.policy.content import markers
from roboco.models.base import TaskType
from roboco.services.gateway.choreographer import Choreographer, ChoreographerDeps


def _make_deps(task_svc: AsyncMock) -> ChoreographerDeps:
    base: dict[str, Any] = {
        "task": task_svc,
        "work_session": AsyncMock(),
        "git": AsyncMock(),
        "a2a": AsyncMock(),
        "journal": AsyncMock(),
        "audit": AsyncMock(),
        "evidence_repo": AsyncMock(),
    }
    base["journal"].has_decision_for_task.return_value = True
    base["journal"].latest_decision_at.return_value = datetime.now(UTC)
    return ChoreographerDeps(**base)


def _budget_blocked_task(*, budget_usd: float | None = 5.0) -> MagicMock:
    t = MagicMock(
        id=uuid4(),
        status="blocked",
        pre_block_state="in_progress",
        pre_block_assignee=uuid4(),
        pre_block_metadata={},
        dependency_ids=[],
        task_type=TaskType.CODE,
        budget_usd=budget_usd,
        orchestration_markers=None,
    )
    markers.mark_budget_blocked(t)
    return t


@pytest.fixture(autouse=True)
def _budgets_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "task_budgets_enabled", True)


@pytest.mark.asyncio
async def test_unblock_refuses_while_still_over_cap() -> None:
    t = _budget_blocked_task(budget_usd=5.0)
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.unmet_dependency_ids.return_value = []
    task_svc.task_spend_usd.return_value = 7.0
    c = Choreographer(_make_deps(task_svc))

    env = await c.unblock(uuid4(), t.id, "attempting to resume")
    body = env.as_dict()
    assert body["error"] == "invalid_state", body
    assert "5.00" in body["message"] and "7.00" in body["message"]
    assert "budget" in body["remediate"].lower()
    task_svc.unblock_with_restore.assert_not_awaited()
    # Marker survives — a retry that hasn't actually cleared the cap must
    # still be caught.
    assert markers.is_budget_blocked(t) is True


@pytest.mark.asyncio
async def test_unblock_succeeds_when_already_under_cap() -> None:
    t = _budget_blocked_task(budget_usd=5.0)
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.unmet_dependency_ids.return_value = []
    task_svc.task_spend_usd.return_value = 3.0
    task_svc.unblock_with_restore.return_value = t
    c = Choreographer(_make_deps(task_svc))

    env = await c.unblock(uuid4(), t.id, "spend never actually breached")
    body = env.as_dict()
    assert body.get("error") is None, body
    task_svc.unblock_with_restore.assert_awaited_once()
    assert markers.is_budget_blocked(t) is False


@pytest.mark.asyncio
async def test_raise_then_unblock_succeeds_after_a_prior_refusal() -> None:
    """First attempt: still over -> refused. CEO raises budget_usd. Second
    attempt: now under -> succeeds, marker cleared."""
    t = _budget_blocked_task(budget_usd=5.0)
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.unmet_dependency_ids.return_value = []
    task_svc.task_spend_usd.return_value = 7.0
    task_svc.unblock_with_restore.return_value = t
    c = Choreographer(_make_deps(task_svc))
    pm_id = uuid4()

    refused = await c.unblock(pm_id, t.id, "attempting resume")
    assert refused.as_dict()["error"] == "invalid_state"
    task_svc.unblock_with_restore.assert_not_awaited()

    # CEO raises the cap; re-block for a fresh attempt (the same task row, as
    # it would be across two real requests).
    t.budget_usd = 20.0
    t.status = "blocked"
    succeeded = await c.unblock(pm_id, t.id, "raised the budget, resuming")
    body = succeeded.as_dict()
    assert body.get("error") is None, body
    task_svc.unblock_with_restore.assert_awaited_once()
    assert markers.is_budget_blocked(t) is False


@pytest.mark.asyncio
async def test_flag_off_ignores_the_marker(monkeypatch: pytest.MonkeyPatch) -> None:
    """The marker alone must never gate anything with the flag off."""
    monkeypatch.setattr(settings, "task_budgets_enabled", False)
    t = _budget_blocked_task(budget_usd=5.0)
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.unmet_dependency_ids.return_value = []
    task_svc.unblock_with_restore.return_value = t
    c = Choreographer(_make_deps(task_svc))

    env = await c.unblock(uuid4(), t.id, "flag is off")
    assert env.as_dict().get("error") is None, env.as_dict()
    task_svc.task_spend_usd.assert_not_awaited()


@pytest.mark.asyncio
async def test_non_budget_block_never_reaches_the_spend_query() -> None:
    """A task blocked for an ordinary reason (no BUDGET_BLOCKED marker) skips
    the guard entirely — it's not a budget block at all."""
    t = MagicMock(
        id=uuid4(),
        status="blocked",
        pre_block_state="in_progress",
        pre_block_assignee=uuid4(),
        pre_block_metadata={},
        dependency_ids=[],
        task_type=TaskType.CODE,
        budget_usd=5.0,
        orchestration_markers=None,
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.unmet_dependency_ids.return_value = []
    task_svc.unblock_with_restore.return_value = t
    c = Choreographer(_make_deps(task_svc))

    env = await c.unblock(uuid4(), t.id, "manual escalation resolved")
    assert env.as_dict().get("error") is None, env.as_dict()
    task_svc.task_spend_usd.assert_not_awaited()
