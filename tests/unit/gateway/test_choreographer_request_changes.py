"""Choreographer.request_changes — the PM merge-level reject (S6 gap B4).

At awaiting_pm_review the PM previously had only complete/escalate, so an AC
violation caught at merge review looped i_am_blocked→escalate. request_changes
routes it to needs_revision with concrete issues and a2a-delivers the reason.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.services.gateway.choreographer import Choreographer, ChoreographerDeps


def _make_deps(**overrides: Any) -> ChoreographerDeps:
    base = {
        "task": AsyncMock(),
        "work_session": AsyncMock(),
        "git": AsyncMock(),
        "a2a": AsyncMock(),
        "journal": AsyncMock(),
        "audit": AsyncMock(),
        "evidence_repo": AsyncMock(),
    }
    base.update(overrides)
    repo = base["evidence_repo"]
    for method in (
        "list_unread_a2a",
        "list_unread_mentions",
        "list_pending_notifications",
        "task_metadata_gaps",
        "recent_team_activity",
        "blockers_in_lane",
        "journal_highlights_for_task",
    ):
        getattr(repo, method).return_value = []
    _ldef = base["journal"].latest_decision_at.return_value
    if type(_ldef).__name__ in ("MagicMock", "AsyncMock"):
        base["journal"].latest_decision_at.return_value = datetime.now(UTC)
    return ChoreographerDeps(**base)


def _pm_review_task(task_id: Any, assigned_to: Any) -> MagicMock:
    return MagicMock(
        id=task_id,
        status="awaiting_pm_review",
        assigned_to=assigned_to,
        team="frontend",
        task_type="code",
        pr_number=176,
        branch_name="feature/frontend/abc--def--ghi",
        orchestration_markers=None,
    )


def _pm_agent_mock(pm_id: Any, role: str = "cell_pm") -> MagicMock:
    agent = MagicMock(id=pm_id, team="frontend", slug="fe-pm")
    agent.role = role
    return agent


@pytest.mark.asyncio
async def test_request_changes_succeeds_and_notifies_new_owner() -> None:
    pm_id = uuid4()
    task_id = uuid4()
    dev_id = uuid4()
    t = _pm_review_task(task_id, pm_id)
    after = MagicMock(
        id=task_id,
        status="needs_revision",
        assigned_to=dev_id,
        team="frontend",
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.agent_for.return_value = _pm_agent_mock(pm_id)
    task_svc.request_changes.return_value = after
    task_svc.session = MagicMock()
    task_svc.session.add = MagicMock()
    task_svc.session.flush = AsyncMock()
    task_svc.session.begin_nested = MagicMock(
        return_value=MagicMock(
            __aenter__=AsyncMock(return_value=None),
            __aexit__=AsyncMock(return_value=False),
        )
    )
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    issues = [
        "frontend/CLAUDE.md modified out of scope — revert the doc commit hunk",
    ]
    env = await c.request_changes(pm_id, task_id, issues)
    assert env.error is None
    assert env.status == "needs_revision"
    task_svc.request_changes.assert_awaited_once()
    # The ledger insert ran (findings=[the shimmed issue]) before the transition.
    task_svc.session.add.assert_called_once()


@pytest.mark.asyncio
async def test_request_changes_requires_at_least_one_issue() -> None:
    pm_id = uuid4()
    task_id = uuid4()
    t = _pm_review_task(task_id, pm_id)
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.agent_for.return_value = _pm_agent_mock(pm_id)
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.request_changes(pm_id, task_id, issues=[])
    body = env.as_dict()
    assert body["error"] == "invalid_state"
    assert "finding" in body["message"].lower()


@pytest.mark.asyncio
async def test_request_changes_rejected_outside_pm_review() -> None:
    pm_id = uuid4()
    task_id = uuid4()
    t = _pm_review_task(task_id, pm_id)
    t.status = "in_progress"
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.agent_for.return_value = _pm_agent_mock(pm_id)
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.request_changes(pm_id, task_id, issues=["real issue here"])
    body = env.as_dict()
    assert body["error"] is not None
    task_svc.request_changes.assert_not_awaited()


@pytest.mark.asyncio
async def test_request_changes_rejected_for_non_pm_role() -> None:
    dev_id = uuid4()
    task_id = uuid4()
    t = _pm_review_task(task_id, dev_id)
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.agent_for.return_value = _pm_agent_mock(dev_id, role="developer")
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.request_changes(dev_id, task_id, issues=["real issue here"])
    body = env.as_dict()
    assert body["error"] is not None
    task_svc.request_changes.assert_not_awaited()
