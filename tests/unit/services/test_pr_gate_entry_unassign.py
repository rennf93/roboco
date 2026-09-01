"""PR-gate entry ownership round-trip against a real Postgres DB.

``submit_for_review`` (the composed action of ``submit_up`` / ``submit_root``)
used to leave the submitting PM's ``assigned_to`` / ``claimed_by`` /
``active_claimant_id`` on the task after it entered ``awaiting_pr_review``,
so the panel showed the PM still owning a task that was actually waiting in
the reviewer queue (the CEO manually reassigned gate tasks to PR reviewers
2026-09-01). These prove the clear persists and a reviewer's
``pr_gate_claim`` lands cleanly on the unassigned row.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import pytest
from roboco.db.tables import AgentTable, ProjectTable, TaskTable
from roboco.models.base import (
    AgentRole,
    AgentStatus,
    TaskStatus,
    TaskType,
    Team,
)
from roboco.services.task import TaskService

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


async def _seed(session: AsyncSession) -> tuple[UUID, UUID]:
    """Seed a cell PM agent + project. Returns ``(project_id, pm_agent_id)``."""
    pm = AgentTable(
        id=uuid4(),
        name="Cell PM",
        slug=f"cell-pm-{uuid4().hex[:8]}",
        role=AgentRole.CELL_PM,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="pm",
        capabilities=[],
        permissions={},
        metrics={},
    )
    session.add(pm)
    await session.flush()

    project = ProjectTable(
        id=uuid4(),
        name="Gate Entry Test Project",
        slug=f"gateentry-{uuid4().hex[:8]}",
        git_url="https://github.com/example/gateentry.git",
        default_branch="main",
        protected_branches=["main"],
        assigned_cell=Team.BACKEND,
        created_by=pm.id,
        is_active=True,
    )
    session.add(project)
    await session.flush()
    return UUID(str(project.id)), UUID(str(pm.id))


async def _in_progress_task(
    session: AsyncSession, project_id: UUID, pm_id: UUID
) -> TaskTable:
    """A code task owned by the PM at in_progress (the submit_up entry state)."""
    task = TaskTable(
        id=uuid4(),
        title="Gate entry ownership test",
        description="Assembled cell task heading for the PR-review gate.",
        acceptance_criteria=["AC: gate entry clears the PM assignee"],
        acceptance_criteria_ids=["ac-1"],
        parent_ac_refs=[],
        task_type=TaskType.CODE,
        status=TaskStatus.IN_PROGRESS,
        created_by=pm_id,
        priority=2,
        sequence=0,
        project_id=project_id,
        team=Team.BACKEND,
        assigned_to=pm_id,
        claimed_by=pm_id,
        active_claimant_id=pm_id,
        branch_name="feature/backend/gatetest",
    )
    session.add(task)
    await session.flush()
    return task


@pytest.mark.asyncio
async def test_submit_for_review_clears_pm_ownership(
    db_session: AsyncSession,
) -> None:
    project_id, pm_id = await _seed(db_session)
    task = await _in_progress_task(db_session, project_id, pm_id)

    svc = TaskService(db_session)
    result = await svc.submit_for_review(pm_id, UUID(str(task.id)), notes="assembled")

    assert result is not None
    assert result.status == TaskStatus.AWAITING_PR_REVIEW
    assert result.assigned_to is None
    assert result.claimed_by is None
    assert result.active_claimant_id is None
