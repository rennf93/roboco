"""Tests for the developer-facing Choreographer methods."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.services.gateway.choreographer import Choreographer, ChoreographerDeps


def _make_deps(**overrides: AsyncMock) -> ChoreographerDeps:
    task = overrides.get("task", AsyncMock())
    work_session = overrides.get("work_session", AsyncMock())
    git = overrides.get("git", AsyncMock())
    a2a = overrides.get("a2a", AsyncMock())
    journal = overrides.get("journal", AsyncMock())
    audit = overrides.get("audit", AsyncMock())
    evidence_repo = overrides.get("evidence_repo", AsyncMock())
    # Ensure evidence_repo returns empty lists by default
    for method in (
        "list_unread_a2a",
        "list_unread_mentions",
        "list_pending_notifications",
        "task_metadata_gaps",
        "recent_team_activity",
        "blockers_in_lane",
    ):
        getattr(evidence_repo, method).return_value = []
    return ChoreographerDeps(
        task=task,
        work_session=work_session,
        git=git,
        a2a=a2a,
        journal=journal,
        audit=audit,
        evidence_repo=evidence_repo,
    )


@pytest.mark.asyncio
async def test_give_me_work_returns_assigned_task() -> None:
    agent_id = uuid4()
    task_obj = MagicMock(id=uuid4(), status="pending", title="t1")
    task_svc = AsyncMock()
    task_svc.list_assigned_for_agent.return_value = [task_obj]
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.give_me_work(agent_id)
    body = env.as_dict()
    assert body["status"] == "pending"
    assert body["task_id"] == str(task_obj.id)
    assert "i_will_work_on" in body["next"]


@pytest.mark.asyncio
async def test_give_me_work_returns_paused_when_no_assigned() -> None:
    agent_id = uuid4()
    paused_obj = MagicMock(id=uuid4(), status="paused")
    task_svc = AsyncMock()
    task_svc.list_assigned_for_agent.return_value = []
    task_svc.list_paused_for_agent.return_value = [paused_obj]
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.give_me_work(agent_id)
    body = env.as_dict()
    assert body["task_id"] == str(paused_obj.id)
    assert "resume" in body["next"]


@pytest.mark.asyncio
async def test_give_me_work_returns_idle_when_no_work() -> None:
    agent_id = uuid4()
    task_svc = AsyncMock()
    task_svc.list_assigned_for_agent.return_value = []
    task_svc.list_paused_for_agent.return_value = []
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.give_me_work(agent_id)
    body = env.as_dict()
    assert body["status"] == "idle"
    assert "i_am_idle" in body["next"]
