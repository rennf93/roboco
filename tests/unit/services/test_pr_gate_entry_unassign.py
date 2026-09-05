"""PR-gate entry ownership round-trip against a real Postgres DB.

``submit_for_review`` (the composed action of ``submit_up`` / ``submit_root``)
used to leave the submitting PM's ``assigned_to`` / ``claimed_by`` /
``active_claimant_id`` on the task after it entered ``awaiting_pr_review``,
so the panel showed the PM still owning a task that was actually waiting in
the reviewer queue (the CEO manually reassigned gate tasks to PR reviewers
2026-09-01). These prove the clear persists and a reviewer's
``pr_gate_claim`` lands cleanly on the unassigned row.

The clear used to be the END of the story: nothing ever reassigned the
task to a reviewer, so it sat with ``assigned_to`` NULL instead of naming
the reviewer that would eventually claim it. ``TaskService.pr_reviewer_for``
plus the choreographer's ``_notify_pr_reviewer`` close that gap; the tests
below cover the resolution helper directly and the full clear-then-reassign
round trip.
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


async def _seed_reviewer(session: AsyncSession, slug: str, team: Team) -> AgentTable:
    """Seed a PR_REVIEWER-role agent row with the given slug/team."""
    reviewer = AgentTable(
        id=uuid4(),
        name=slug,
        slug=slug,
        role=AgentRole.PR_REVIEWER,
        team=team,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="review",
        capabilities=[],
        permissions={},
        metrics={},
    )
    session.add(reviewer)
    await session.flush()
    return reviewer


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


@pytest.mark.asyncio
async def test_pr_reviewer_for_returns_cell_reviewer_for_backend_task(
    db_session: AsyncSession,
) -> None:
    """A backend-team gate task (cell→root) resolves to be-pr-reviewer."""
    project_id, pm_id = await _seed(db_session)
    reviewer = await _seed_reviewer(db_session, "be-pr-reviewer", Team.BACKEND)
    task = await _in_progress_task(db_session, project_id, pm_id)

    svc = TaskService(db_session)
    resolved = await svc.pr_reviewer_for(task)

    assert resolved is not None
    assert UUID(str(resolved.id)) == UUID(str(reviewer.id))


@pytest.mark.asyncio
async def test_pr_reviewer_for_returns_pr_reviewer_1_for_root_task(
    db_session: AsyncSession,
) -> None:
    """A root (main_pm-team) gate task resolves to the shared pr-reviewer-1,
    unambiguously, even though cell-pr-reviewer-2 shares its board team and
    role, the root case is resolved by exact slug, not role+team."""
    project_id, pm_id = await _seed(db_session)
    main_reviewer = await _seed_reviewer(db_session, "pr-reviewer-1", Team.BOARD)
    await _seed_reviewer(db_session, "cell-pr-reviewer-2", Team.BOARD)
    task = await _in_progress_task(db_session, project_id, pm_id)
    task.team = Team.MAIN_PM
    await db_session.flush()

    svc = TaskService(db_session)
    resolved = await svc.pr_reviewer_for(task)

    assert resolved is not None
    assert UUID(str(resolved.id)) == UUID(str(main_reviewer.id))


@pytest.mark.asyncio
async def test_gate_entry_clear_then_reassign_lands_on_cell_reviewer(
    db_session: AsyncSession,
) -> None:
    """The full clear-then-reassign round trip: submit_for_review clears
    the PM, then reassigning to pr_reviewer_for's pick (mirroring the
    choreographer's ``_notify_pr_reviewer``) lands assigned_to on the real
    reviewer instead of leaving it NULL."""
    project_id, pm_id = await _seed(db_session)
    reviewer = await _seed_reviewer(db_session, "be-pr-reviewer", Team.BACKEND)
    task = await _in_progress_task(db_session, project_id, pm_id)
    task_id = UUID(str(task.id))

    svc = TaskService(db_session)
    gated = await svc.submit_for_review(pm_id, task_id, notes="assembled")
    assert gated is not None
    assert gated.assigned_to is None

    resolved = await svc.pr_reviewer_for(gated)
    assert resolved is not None
    result = await svc.reassign(task_id, UUID(str(resolved.id)))

    assert result is not None
    assert UUID(str(result.assigned_to)) == UUID(str(reviewer.id))


@pytest.mark.asyncio
async def test_pr_gate_claim_accepts_overflow_reviewer_over_primary_assignment(
    db_session: AsyncSession,
) -> None:
    """``reassign`` (via ``_notify_pr_reviewer`` at gate entry) sets
    assigned_to/claimed_by to the primary reviewer but leaves
    active_claimant_id untouched (no real claim yet). ``pr_gate_claim``'s
    single-claimant guard reads ONLY active_claimant_id, so the overflow
    reviewer must still be able to claim the task even though
    assigned_to/claimed_by still name the primary."""
    project_id, pm_id = await _seed(db_session)
    primary = await _seed_reviewer(db_session, "be-pr-reviewer", Team.BACKEND)
    overflow = await _seed_reviewer(db_session, "cell-pr-reviewer-2", Team.BOARD)
    task = await _in_progress_task(db_session, project_id, pm_id)
    task_id = UUID(str(task.id))

    svc = TaskService(db_session)
    gated = await svc.submit_for_review(pm_id, task_id, notes="assembled")
    assert gated is not None
    entry = await svc.reassign(task_id, UUID(str(primary.id)))
    assert entry is not None
    assert entry.active_claimant_id is None

    claimed = await svc.pr_gate_claim(UUID(str(overflow.id)), task_id)

    assert claimed is not None
    assert UUID(str(claimed.assigned_to)) == UUID(str(overflow.id))
    assert UUID(str(claimed.claimed_by)) == UUID(str(overflow.id))
    assert UUID(str(claimed.active_claimant_id)) == UUID(str(overflow.id))
