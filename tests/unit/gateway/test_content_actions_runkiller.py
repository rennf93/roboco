"""Real-DB tests for content-action run-killers.

Three contracts, all exercised against the live test Postgres so the bug
boundaries (heartbeat write, claimant gate, plan-step soft-warn) are real:

1. HEARTBEAT-ON-SUCCESS — commit() must refresh ``last_heartbeat_at`` on the
   success path, not only on rejection. Without it an actively-committing
   agent looks idle to the reaper between verb successes.
2. CLAIM-OWNERSHIP — commit()/progress() must verify the caller is the active
   claimant (``active_claimant_id``), not merely the historical ``assigned_to``.
   A reaped/stale assignee whose claim was released must not be able to write.
3. PROGRESS SOFT-WARN — progress() with no ``plan_step`` on a stepped task is
   accepted (product decision) but emits a warning.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
import structlog
from roboco.db.tables import AgentTable, ProjectTable, TaskTable
from roboco.models.base import (
    AgentRole,
    AgentStatus,
    Complexity,
    TaskNature,
    TaskStatus,
    TaskType,
    Team,
)
from roboco.services.gateway.content_actions import ContentActions, ContentActionsDeps
from roboco.services.task import TaskService

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


async def _seed_dev_agent(session: AsyncSession, slug_prefix: str) -> UUID:
    agent = AgentTable(
        id=uuid4(),
        name="Backend Dev",
        slug=f"{slug_prefix}-{uuid4().hex[:8]}",
        role=AgentRole.DEVELOPER,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="dev",
        capabilities=["python"],
        permissions={},
        metrics={},
    )
    session.add(agent)
    await session.flush()
    return UUID(str(agent.id))


async def _seed_claimed_task(
    session: AsyncSession,
    *,
    assigned_to: UUID,
    active_claimant_id: UUID | None,
    plan: dict | None,
    status: TaskStatus = TaskStatus.IN_PROGRESS,
) -> UUID:
    """Seed a project + system creator + an in-progress task and return its id."""
    system_agent = AgentTable(
        id=uuid4(),
        name="System",
        slug=f"system-{uuid4().hex[:8]}",
        role=AgentRole.SYSTEM,
        team=None,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="system",
        capabilities=[],
        permissions={},
        metrics={},
    )
    session.add(system_agent)
    await session.flush()

    project = ProjectTable(
        id=uuid4(),
        name="Run-killer Test Project",
        slug=f"runkiller-{uuid4().hex[:8]}",
        git_url="https://github.com/example/runkiller.git",
        default_branch="main",
        protected_branches=["main"],
        assigned_cell=Team.BACKEND,
        created_by=system_agent.id,
        is_active=True,
    )
    session.add(project)
    await session.flush()

    task = TaskTable(
        id=uuid4(),
        title="Run-killer target task",
        description="Synthetic task for content-action run-killer tests.",
        acceptance_criteria=["content actions behave"],
        status=status,
        priority=2,
        task_type=TaskType.CODE,
        nature=TaskNature.TECHNICAL,
        project_id=project.id,
        branch_name="feature/backend/RUNKILL1",
        created_by=system_agent.id,
        assigned_to=assigned_to,
        active_claimant_id=active_claimant_id,
        team=Team.BACKEND,
        dependency_ids=[],
        blocker_ids=[],
        sequence=0,
        plan=plan,
        estimated_complexity=Complexity.LOW,
        checkpoints=[],
        progress_updates=[],
        commits=[],
        documents=[],
        last_heartbeat_at=None,
    )
    session.add(task)
    await session.commit()
    return UUID(str(task.id))


def _content_actions(session: AsyncSession) -> ContentActions:
    """ContentActions backed by a real TaskService; only git is faked.

    The bug boundaries (heartbeat write, claimant gate) live in
    ContentActions + TaskService against the DB — git is intentionally the
    one faked dependency since these tests don't exercise real git work.
    """
    git = AsyncMock()
    git.commit.return_value = {"sha": "abc12345def"}
    return ContentActions(
        ContentActionsDeps(
            task=TaskService(session),
            git=git,
            messaging=AsyncMock(),
            a2a=AsyncMock(),
            journal=AsyncMock(),
            workspace=AsyncMock(),
            notifications=AsyncMock(),
            notification_delivery=AsyncMock(),
            evidence_repo=AsyncMock(),
        )
    )


@pytest.mark.asyncio
async def test_commit_refreshes_heartbeat_on_success(db_session: AsyncSession) -> None:
    """commit() success must advance last_heartbeat_at past the claim time.

    Regression: the success path returned ok() without touching the
    heartbeat, so an actively-committing agent looked idle to the reaper.
    """
    agent_id = await _seed_dev_agent(db_session, "be-dev")
    task_id = await _seed_claimed_task(
        db_session,
        assigned_to=agent_id,
        active_claimant_id=agent_id,
        plan={"steps": ["build"]},
    )
    svc = TaskService(db_session)
    # Stamp an explicitly-old heartbeat so the success-path refresh is
    # unambiguous (no reliance on sub-millisecond clock resolution).
    stale = datetime.now(UTC) - timedelta(minutes=5)
    row = await svc.get(task_id)
    assert row is not None
    row.last_heartbeat_at = stale
    await db_session.commit()

    ca = _content_actions(db_session)
    env = await ca.commit(
        agent_id=agent_id,
        message="feat(api): add /healthz endpoint for liveness checks",
    )
    await db_session.commit()

    assert env.as_dict()["error"] is None
    refreshed = await svc.get(task_id)
    assert refreshed is not None
    assert refreshed.last_heartbeat_at is not None
    assert refreshed.last_heartbeat_at > stale, (
        "commit() must refresh the claimant heartbeat on success, "
        "not only on the rejection path"
    )


@pytest.mark.asyncio
async def test_commit_rejected_when_claim_released(db_session: AsyncSession) -> None:
    """A stale/reaped assignee whose active claim was cleared cannot commit.

    assigned_to still points at the old agent, but active_claimant_id is
    NULL (claim released by the reaper). The historical assignee must be
    refused with not_authorized rather than writing onto a freed task.
    """
    agent_id = await _seed_dev_agent(db_session, "be-dev")
    task_id = await _seed_claimed_task(
        db_session,
        assigned_to=agent_id,
        active_claimant_id=None,
        plan={"steps": ["build"]},
    )

    ca = _content_actions(db_session)
    env = await ca.commit(
        agent_id=agent_id,
        message="feat(api): add /healthz endpoint for liveness checks",
    )
    body = env.as_dict()

    assert body["error"] == "not_authorized", (
        "an assignee whose active claim was released must not commit"
    )
    _ = task_id


@pytest.mark.asyncio
async def test_commit_rejected_when_another_agent_holds_claim(
    db_session: AsyncSession,
) -> None:
    """Another agent holding the active claim blocks the historical assignee."""
    old_agent = await _seed_dev_agent(db_session, "be-dev")
    new_agent = await _seed_dev_agent(db_session, "be-dev")
    await _seed_claimed_task(
        db_session,
        assigned_to=old_agent,
        active_claimant_id=new_agent,
        plan={"steps": ["build"]},
    )

    ca = _content_actions(db_session)
    env = await ca.commit(
        agent_id=old_agent,
        message="feat(api): add /healthz endpoint for liveness checks",
    )
    body = env.as_dict()

    assert body["error"] == "not_authorized"


@pytest.mark.asyncio
async def test_progress_no_plan_step_on_stepped_task_is_accepted(
    db_session: AsyncSession,
) -> None:
    """progress() with no plan_step on a stepped task is accepted + warns.

    Product decision: a narrative progress entry without a plan_step on a
    task that has plan sub_tasks is allowed (not rejected), but emits a
    soft warning so the gap is visible.
    """
    agent_id = await _seed_dev_agent(db_session, "be-dev")
    task_id = await _seed_claimed_task(
        db_session,
        assigned_to=agent_id,
        active_claimant_id=agent_id,
        plan={"sub_tasks": [{"id": "s1", "title": "build"}]},
    )

    ca = _content_actions(db_session)
    with structlog.testing.capture_logs() as logs:
        env = await ca.progress(
            agent_id=agent_id,
            task_id=task_id,
            message="made some mid-step progress without finishing a step",
        )
    await db_session.commit()

    body = env.as_dict()
    assert body["error"] is None, "missing plan_step on a stepped task must be accepted"
    assert body["task_id"] == str(task_id)
    assert any(
        entry.get("log_level") == "warning"
        and "plan_step" in str(entry.get("event", ""))
        for entry in logs
    ), "a soft warning must be emitted when plan_step is omitted on a stepped task"
