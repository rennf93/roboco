"""TaskService coverage — status transitions + claim/unclaim/cancel paths.

Covers the core lifecycle methods (start, claim, unclaim variants, pause,
resume, cancel cascades, block/unblock, fail_qa with original-developer
reassignment, ceo_approve/ceo_reject, escalation chains).
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import pytest_asyncio
from roboco.db.tables import AgentTable, ProjectTable
from roboco.events import EventType
from roboco.models import AgentRole, AgentStatus, Team
from roboco.models.base import (
    BlockerResolverType,
    Complexity,
    TaskNature,
    TaskStatus,
    TaskType,
)
from roboco.models.task import TaskCreateRequest
from roboco.services.base import NotFoundError
from roboco.services.task import SoftBlockInfo, TaskService

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession


@pytest_asyncio.fixture
async def task_setup(
    db_session: AsyncSession,
) -> AsyncIterator[dict]:
    agent = AgentTable(
        id=uuid4(),
        name="Dev",
        slug=f"be-dev-{uuid4().hex[:8]}",
        role=AgentRole.DEVELOPER,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="dev",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add(agent)
    await db_session.flush()
    project = ProjectTable(
        id=uuid4(),
        name="T-Proj",
        slug=f"t-proj-{uuid4().hex[:8]}",
        git_url="https://example.com/r.git",
        assigned_cell=Team.BACKEND,
        created_by=agent.id,
    )
    db_session.add(project)
    await db_session.flush()
    yield {
        "svc": TaskService(db_session),
        "agent_id": agent.id,
        "project_id": project.id,
        "db": db_session,
    }


def _req(setup: dict, **overrides) -> TaskCreateRequest:
    return TaskCreateRequest(
        title=overrides.pop("title", "t"),
        description=overrides.pop("description", "d"),
        acceptance_criteria=overrides.pop("acceptance_criteria", ["ac"]),
        team=overrides.pop("team", Team.BACKEND),
        created_by=setup["agent_id"],
        project_id=setup["project_id"],
        task_type=overrides.pop("task_type", TaskType.CODE),
        nature=overrides.pop("nature", TaskNature.TECHNICAL),
        estimated_complexity=overrides.pop("estimated_complexity", Complexity.MEDIUM),
        **overrides,
    )


# ---------------------------------------------------------------------------
# start()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_returns_none_for_missing(task_setup: dict) -> None:
    svc = task_setup["svc"]
    assert await svc.start(uuid4()) is None


@pytest.mark.asyncio
async def test_start_returns_none_when_invalid_status(
    task_setup: dict,
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))  # PENDING
    assert await svc.start(task.id, agent_role="developer") is None


@pytest.mark.asyncio
async def test_start_returns_none_when_no_plan(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.CLAIMED
    task.plan = None
    await db_session.flush()
    assert await svc.start(task.id, agent_role="developer") is None


@pytest.mark.asyncio
async def test_start_with_plan_advances_to_in_progress(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.CLAIMED
    task.assigned_to = task_setup["agent_id"]
    task.branch_name = "feature/backend/AAAAAAA"
    task.plan = {"text": "step 1"}
    await db_session.flush()
    started = await svc.start(
        task.id, agent_id=task_setup["agent_id"], agent_role="developer"
    )
    assert started is not None
    assert started.status == TaskStatus.IN_PROGRESS
    assert started.started_at is not None


@pytest.mark.asyncio
async def test_start_returns_none_when_ownership_fails(
    task_setup: dict, db_session: AsyncSession
) -> None:
    """Non-assignee agent cannot start the task."""
    svc = task_setup["svc"]
    other = AgentTable(
        id=uuid4(),
        name="Other",
        slug=f"other-{uuid4().hex[:8]}",
        role=AgentRole.DEVELOPER,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="x",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add(other)
    await db_session.flush()
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.CLAIMED
    task.assigned_to = other.id
    task.plan = {"text": "p"}
    await db_session.flush()
    out = await svc.start(
        task.id, agent_id=task_setup["agent_id"], agent_role="developer"
    )
    assert out is None


@pytest.mark.asyncio
async def test_start_paused_task_resumes_in_progress(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.PAUSED
    task.assigned_to = task_setup["agent_id"]
    task.plan = {"text": "p"}
    await db_session.flush()
    started = await svc.start(task.id, agent_role="developer")
    assert started is not None
    assert started.status == TaskStatus.IN_PROGRESS


# ---------------------------------------------------------------------------
# unclaim_for_reaper
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unclaim_for_reaper_resets_claimed_task(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.CLAIMED
    task.assigned_to = task_setup["agent_id"]
    await db_session.flush()
    await svc.unclaim_for_reaper(task.id)
    refreshed = await svc.get(task.id)
    assert refreshed is not None
    assert refreshed.status == TaskStatus.PENDING
    assert refreshed.assigned_to is None


@pytest.mark.asyncio
async def test_unclaim_for_reaper_skips_when_status_already_pending(
    task_setup: dict,
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))  # PENDING
    # Should not raise — branch returns immediately.
    await svc.unclaim_for_reaper(task.id)


# ---------------------------------------------------------------------------
# unclaim_for_agent — all error paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unclaim_for_agent_returns_none_when_wrong_assignee(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    other = AgentTable(
        id=uuid4(),
        name="Other",
        slug=f"other-{uuid4().hex[:8]}",
        role=AgentRole.DEVELOPER,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="x",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add(other)
    await db_session.flush()
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.CLAIMED
    task.assigned_to = other.id
    await db_session.flush()
    assert await svc.unclaim_for_agent(task.id, agent_id=task_setup["agent_id"]) is None


@pytest.mark.asyncio
async def test_unclaim_for_agent_releases_pending_assignment(
    task_setup: dict, db_session: AsyncSession
) -> None:
    """#176: an agent assigned a pending task it never claimed must be
    able to unclaim it (escape the pending-assigned trap). The row stays
    pending; only the assignment is released so the dispatcher can
    reassign it instead of orphaning the task + looping the agent."""
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    task.assigned_to = task_setup["agent_id"]
    await db_session.flush()
    # status is PENDING, never claimed — the smoke-17 trap scenario.
    out = await svc.unclaim_for_agent(task.id, agent_id=task_setup["agent_id"])
    assert out is not None
    assert out.status == TaskStatus.PENDING
    assert out.assigned_to is None
    assert out.active_claimant_id is None


@pytest.mark.asyncio
async def test_unclaim_for_agent_returns_none_when_paused(
    task_setup: dict, db_session: AsyncSession
) -> None:
    """A non-pending/claimed/in_progress status (e.g. paused — which has
    `resume`, not `unclaim`) is still not unclaimable."""
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.PAUSED
    task.assigned_to = task_setup["agent_id"]
    await db_session.flush()
    assert await svc.unclaim_for_agent(task.id, agent_id=task_setup["agent_id"]) is None


# ---------------------------------------------------------------------------
# resume_for_agent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resume_for_agent_returns_none_when_wrong_assignee(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    other = AgentTable(
        id=uuid4(),
        name="Other",
        slug=f"other-{uuid4().hex[:8]}",
        role=AgentRole.DEVELOPER,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="x",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add(other)
    await db_session.flush()
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.PAUSED
    task.assigned_to = other.id
    await db_session.flush()
    assert await svc.resume_for_agent(task.id, agent_id=task_setup["agent_id"]) is None


@pytest.mark.asyncio
async def test_resume_for_agent_returns_none_when_not_paused(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.IN_PROGRESS
    task.assigned_to = task_setup["agent_id"]
    await db_session.flush()
    assert await svc.resume_for_agent(task.id, agent_id=task_setup["agent_id"]) is None


@pytest.mark.asyncio
async def test_resume_for_agent_advances_to_in_progress(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.PAUSED
    task.assigned_to = task_setup["agent_id"]
    await db_session.flush()
    out = await svc.resume_for_agent(task.id, agent_id=task_setup["agent_id"])
    assert out is not None
    assert out.status == TaskStatus.IN_PROGRESS


# ---------------------------------------------------------------------------
# block / unblock with task-dependency blocker
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_block_returns_none_for_missing(task_setup: dict) -> None:
    svc = task_setup["svc"]
    blocker = await svc.create(_req(task_setup))
    assert await svc.block(uuid4(), blocker_task_id=blocker.id) is None


@pytest.mark.asyncio
async def test_block_adds_to_dependencies(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.IN_PROGRESS
    task.assigned_to = task_setup["agent_id"]
    await db_session.flush()
    blocker = await svc.create(_req(task_setup))
    blocked = await svc.block(task.id, blocker_task_id=blocker.id)
    assert blocked is not None
    assert blocked.status == TaskStatus.BLOCKED
    assert blocker.id in blocked.dependency_ids
    assert blocked.blocker_resolver_type == BlockerResolverType.AGENT


@pytest.mark.asyncio
async def test_block_does_not_duplicate_existing_dep(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    blocker = await svc.create(_req(task_setup))
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.IN_PROGRESS
    task.assigned_to = task_setup["agent_id"]
    task.dependency_ids = [blocker.id]
    await db_session.flush()
    blocked = await svc.block(task.id, blocker_task_id=blocker.id)
    assert blocked is not None
    # Still single occurrence
    assert blocked.dependency_ids.count(blocker.id) == 1


# ---------------------------------------------------------------------------
# soft_block — full happy path including HUMAN resolver type
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_soft_block_with_human_resolver(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.IN_PROGRESS
    task.assigned_to = task_setup["agent_id"]
    await db_session.flush()
    blocked = await svc.soft_block(
        task.id,
        SoftBlockInfo(
            reason="HITL needed",
            blocker_type="question",
            what_needed="answer",
            resolver_type=BlockerResolverType.HUMAN,
        ),
    )
    assert blocked is not None
    assert blocked.status == TaskStatus.BLOCKED
    assert blocked.blocker_resolver_type == BlockerResolverType.HUMAN


@pytest.mark.asyncio
async def test_soft_block_returns_none_when_not_in_progress(
    task_setup: dict,
) -> None:
    """soft_block requires IN_PROGRESS — PENDING task fails the gate."""
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))  # PENDING
    out = await svc.soft_block(
        task.id,
        SoftBlockInfo(reason="x", blocker_type="ext", what_needed="y"),
    )
    assert out is None


# ---------------------------------------------------------------------------
# unblock — when not BLOCKED returns None
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unblock_returns_none_when_not_blocked(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.IN_PROGRESS
    await db_session.flush()
    assert await svc.unblock(task.id) is None


# ---------------------------------------------------------------------------
# pause/resume happy paths verifying side-effects
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pause_in_progress_works(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.IN_PROGRESS
    await db_session.flush()
    out = await svc.pause(task.id)
    assert out is not None
    assert out.status == TaskStatus.PAUSED


@pytest.mark.asyncio
async def test_resume_paused_works(task_setup: dict, db_session: AsyncSession) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.PAUSED
    await db_session.flush()
    out = await svc.resume(task.id)
    assert out is not None
    assert out.status == TaskStatus.IN_PROGRESS


# ---------------------------------------------------------------------------
# fail_qa: original developer reassignment & no-original-developer fallback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fail_qa_reassigns_to_original_developer(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    dev_id = task_setup["agent_id"]
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.AWAITING_QA
    task.quick_context = f"original_developer:{dev_id}"
    await db_session.flush()
    failed = await svc.fail_qa(task.id, notes="missing tests")
    assert failed is not None
    assert failed.status == TaskStatus.NEEDS_REVISION
    assert failed.assigned_to == dev_id


@pytest.mark.asyncio
async def test_fail_qa_with_no_original_dev_unassigns(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.AWAITING_QA
    # No quick_context — extract_original_developer returns None
    await db_session.flush()
    failed = await svc.fail_qa(task.id, notes="needs more")
    assert failed is not None
    assert failed.assigned_to is None


# ---------------------------------------------------------------------------
# ceo_approve / ceo_reject — happy paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ceo_approve_returns_none_when_wrong_status(
    task_setup: dict,
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))  # PENDING
    assert await svc.ceo_approve(task.id) is None


@pytest.mark.asyncio
async def test_ceo_approve_marks_completed(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.AWAITING_CEO_APPROVAL
    task.pr_number = 1
    task.pr_url = "u"
    task.docs_complete = True
    task.pr_created = True
    await db_session.flush()
    approved = await svc.ceo_approve(task.id, notes="approved")
    assert approved is not None
    assert approved.status == TaskStatus.COMPLETED


@pytest.mark.asyncio
async def test_ceo_reject_returns_none_when_wrong_status(
    task_setup: dict,
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))  # PENDING
    assert await svc.ceo_reject(task.id, reason="not aligned") is None


@pytest.mark.asyncio
async def test_ceo_reject_reassigns_to_original_dev(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    dev_id = task_setup["agent_id"]
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.AWAITING_CEO_APPROVAL
    task.quick_context = f"original_developer:{dev_id}"
    await db_session.flush()
    rejected = await svc.ceo_reject(task.id, reason="re-do auth flow")
    assert rejected is not None
    assert rejected.status == TaskStatus.NEEDS_REVISION
    assert rejected.assigned_to == dev_id


@pytest.mark.asyncio
async def test_ceo_reject_clears_assignment_when_no_original_dev(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.AWAITING_CEO_APPROVAL
    task.assigned_to = task_setup["agent_id"]
    # No quick_context — original_dev resolves None
    await db_session.flush()
    rejected = await svc.ceo_reject(task.id, reason="redo")
    assert rejected is not None
    assert rejected.assigned_to is None


# ---------------------------------------------------------------------------
# escalate_to_ceo - all error branches
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_escalate_to_ceo_returns_none_when_wrong_status(
    task_setup: dict,
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))  # PENDING
    out = await svc.escalate_to_ceo(task.id, agent_role="cell_pm", notes="x")
    assert out is None


@pytest.mark.asyncio
async def test_escalate_to_ceo_returns_none_for_subtask(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    parent = await svc.create(_req(task_setup))
    sub = await svc.create(_req(task_setup, parent_task_id=parent.id))
    sub.status = TaskStatus.AWAITING_PM_REVIEW
    sub.pr_number = 1
    await db_session.flush()
    out = await svc.escalate_to_ceo(sub.id, agent_role="cell_pm", notes="x")
    assert out is None


@pytest.mark.asyncio
async def test_escalate_to_ceo_returns_none_when_no_pr(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.AWAITING_PM_REVIEW
    task.pr_number = None
    await db_session.flush()
    out = await svc.escalate_to_ceo(task.id, agent_role="main_pm", notes="x")
    assert out is None


@pytest.mark.asyncio
async def test_escalate_to_ceo_advances_status_with_notes(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.AWAITING_PM_REVIEW
    task.pr_number = 42
    task.pr_url = "https://example.com/pr/42"
    task.pr_created = True
    task.docs_complete = True
    await db_session.flush()
    escalated = await svc.escalate_to_ceo(
        task.id, agent_role="main_pm", notes="needs CEO review for breaking change"
    )
    assert escalated is not None
    assert escalated.status == TaskStatus.AWAITING_CEO_APPROVAL
    assert "escalation_notes" in (escalated.quick_context or "")


# ---------------------------------------------------------------------------
# cancel + cascade
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_skips_already_terminal_descendants(
    task_setup: dict, db_session: AsyncSession
) -> None:
    """Descendants already in COMPLETED/CANCELLED are skipped, not re-cancelled."""
    svc = task_setup["svc"]
    parent = await svc.create(_req(task_setup))
    completed_child = await svc.create(_req(task_setup, parent_task_id=parent.id))
    completed_child.status = TaskStatus.COMPLETED
    pending_child = await svc.create(_req(task_setup, parent_task_id=parent.id))
    await db_session.flush()
    out = await svc.cancel(parent.id, agent_role="cell_pm")
    assert out is not None
    refreshed_completed = await svc.get(completed_child.id)
    assert refreshed_completed is not None
    assert refreshed_completed.status == TaskStatus.COMPLETED
    refreshed_pending = await svc.get(pending_child.id)
    assert refreshed_pending is not None
    assert refreshed_pending.status == TaskStatus.CANCELLED


@pytest.mark.asyncio
async def test_cancel_returns_task_with_cancellation_note_appended(
    task_setup: dict,
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    task.dev_notes = "earlier note"
    out = await svc.cancel(task.id, cancellation_note="duplicate")
    assert out is not None
    assert "duplicate" in (out.dev_notes or "")


# ---------------------------------------------------------------------------
# _unblock_dependents
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unblock_dependents_clears_dep_id(
    task_setup: dict, db_session: AsyncSession
) -> None:
    """Completing a blocker unblocks its dependents."""
    svc = task_setup["svc"]
    blocker = await svc.create(_req(task_setup))
    dependent = await svc.create(_req(task_setup))
    dependent.status = TaskStatus.BLOCKED
    dependent.dependency_ids = [blocker.id]
    await db_session.flush()
    await svc._unblock_dependents(blocker.id)
    refreshed = await svc.get(dependent.id)
    assert refreshed is not None
    assert blocker.id not in refreshed.dependency_ids
    assert refreshed.status == TaskStatus.IN_PROGRESS


@pytest.mark.asyncio
async def test_unblock_dependents_keeps_blocked_when_other_deps_remain(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    blocker = await svc.create(_req(task_setup))
    other_blocker = await svc.create(_req(task_setup))
    dependent = await svc.create(_req(task_setup))
    dependent.status = TaskStatus.BLOCKED
    dependent.dependency_ids = [blocker.id, other_blocker.id]
    await db_session.flush()
    await svc._unblock_dependents(blocker.id)
    refreshed = await svc.get(dependent.id)
    assert refreshed is not None
    # Still blocked because other_blocker is still in deps
    assert refreshed.status == TaskStatus.BLOCKED


# ---------------------------------------------------------------------------
# claim — gate validations (team mismatch, role mismatch, self-review)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_claim_rejects_when_agent_team_mismatch(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    # Create an agent on a different team
    other = AgentTable(
        id=uuid4(),
        name="FE",
        slug=f"fe-dev-{uuid4().hex[:8]}",
        role=AgentRole.DEVELOPER,
        team=Team.FRONTEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="x",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add(other)
    await db_session.flush()
    task = await svc.create(_req(task_setup, team=Team.BACKEND))
    task.branch_name = "feature/backend/x"
    await db_session.flush()
    out = await svc.claim(task.id, other.id)
    assert out is None


@pytest.mark.asyncio
async def test_claim_rejects_self_review_for_qa(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    qa_agent = AgentTable(
        id=uuid4(),
        name="QA",
        slug=f"be-qa-{uuid4().hex[:8]}",
        role=AgentRole.QA,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="qa",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add(qa_agent)
    await db_session.flush()
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.AWAITING_QA
    task.branch_name = "feature/backend/x"
    task.quick_context = f"original_developer:{qa_agent.id}"
    await db_session.flush()
    out = await svc.claim(task.id, qa_agent.id)
    assert out is None


@pytest.mark.asyncio
async def test_claim_management_role_can_claim_any_team(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    main_pm = AgentTable(
        id=uuid4(),
        name="MainPM",
        slug=f"main-pm-{uuid4().hex[:8]}",
        role=AgentRole.MAIN_PM,
        team=Team.MAIN_PM,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="pm",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add(main_pm)
    await db_session.flush()
    task = await svc.create(_req(task_setup, team=Team.BACKEND))
    task.branch_name = "feature/backend/x"
    await db_session.flush()
    claimed = await svc.claim(task.id, main_pm.id)
    assert claimed is not None
    assert claimed.assigned_to == main_pm.id


# ---------------------------------------------------------------------------
# pass_qa with notes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pass_qa_records_notes(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.AWAITING_QA
    task.pr_number = 1
    task.pr_url = "u"
    await db_session.flush()
    passed = await svc.pass_qa(task.id, notes="LGTM detailed", agent_role="qa")
    assert passed is not None
    assert passed.qa_notes == "LGTM detailed"


@pytest.mark.asyncio
async def test_pass_qa_resets_docs_complete(
    task_setup: dict, db_session: AsyncSession
) -> None:
    """Passing QA forces docs_complete=False so the documenter must redo."""
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.AWAITING_QA
    task.pr_number = 1
    task.pr_url = "u"
    task.docs_complete = True
    await db_session.flush()
    passed = await svc.pass_qa(task.id, agent_role="qa")
    assert passed is not None
    assert passed.docs_complete is False


# ---------------------------------------------------------------------------
# claim_seeds_branch via auto-create — failure rollback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_claim_rolls_back_on_branch_failure(
    task_setup: dict, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When _ensure_branch_for_task fails, claim fields revert."""
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    # No branch — claim will try to auto-create.
    await db_session.flush()

    async def _fail(*_args: object, **_kwargs: object) -> str:
        raise ValueError("simulated branch failure")

    monkeypatch.setattr(svc, "_ensure_branch_for_task", _fail)
    with pytest.raises(ValueError, match="simulated branch failure"):
        await svc.claim(task.id, task_setup["agent_id"])

    refreshed = await svc.get(task.id)
    assert refreshed is not None
    # Status rolled back to PENDING
    assert refreshed.status == TaskStatus.PENDING
    assert refreshed.assigned_to is None


# ---------------------------------------------------------------------------
# submit_for_pm_review — full happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_submit_for_pm_review_returns_none_when_not_in_progress(
    task_setup: dict,
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))  # PENDING
    out = await svc.submit_for_pm_review(task.id, agent_role="cell_pm")
    assert out is None


@pytest.mark.asyncio
async def test_submit_for_pm_review_returns_none_when_no_branch(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.IN_PROGRESS
    task.branch_name = None
    await db_session.flush()
    out = await svc.submit_for_pm_review(task.id, agent_role="cell_pm")
    assert out is None


@pytest.mark.asyncio
async def test_submit_for_pm_review_returns_none_when_no_pr(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.IN_PROGRESS
    task.branch_name = "feature/backend/x"
    task.pr_created = False
    await db_session.flush()
    out = await svc.submit_for_pm_review(task.id, agent_role="cell_pm")
    assert out is None


@pytest.mark.asyncio
async def test_submit_for_pm_review_returns_none_when_active_descendants(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    parent = await svc.create(_req(task_setup))
    parent.status = TaskStatus.IN_PROGRESS
    parent.branch_name = "feature/backend/x"
    parent.pr_created = True
    parent.pr_number = 1
    await db_session.flush()
    # Create a non-terminal child
    child = await svc.create(_req(task_setup, parent_task_id=parent.id))
    await db_session.flush()
    assert child is not None  # ensure child exists
    out = await svc.submit_for_pm_review(parent.id, agent_role="cell_pm")
    assert out is None


@pytest.mark.asyncio
async def test_submit_for_pm_review_advances_with_notes(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.IN_PROGRESS
    task.branch_name = "feature/backend/x"
    task.pr_created = True
    task.pr_number = 1
    task.docs_complete = True
    await db_session.flush()
    out = await svc.submit_for_pm_review(
        task.id, agent_role="cell_pm", notes="ready for review"
    )
    assert out is not None
    assert out.status == TaskStatus.AWAITING_PM_REVIEW


# ---------------------------------------------------------------------------
# docs_complete: full path including descendants check
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_docs_complete_returns_none_when_active_descendants(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    parent = await svc.create(_req(task_setup))
    parent.status = TaskStatus.AWAITING_DOCUMENTATION
    parent.assigned_to = task_setup["agent_id"]
    parent.pr_number = 1
    parent.pr_url = "u"
    await db_session.flush()
    child = await svc.create(_req(task_setup, parent_task_id=parent.id))
    await db_session.flush()
    assert child is not None
    out = await svc.docs_complete(parent.id, doc_notes="docs done")
    assert out is None


@pytest.mark.asyncio
async def test_docs_complete_returns_none_when_invalid_status(
    task_setup: dict,
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))  # PENDING
    out = await svc.docs_complete(task.id, doc_notes="docs")
    assert out is None


@pytest.mark.asyncio
async def test_docs_complete_advances_when_pr_already_created(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.AWAITING_DOCUMENTATION
    task.assigned_to = task_setup["agent_id"]
    task.pr_number = 1
    task.pr_url = "u"
    task.pr_created = True
    await db_session.flush()
    out = await svc.docs_complete(task.id, doc_notes="documented all flows")
    assert out is not None
    assert out.docs_complete is True
    # Both flags now true → advances to AWAITING_PM_REVIEW
    assert out.status == TaskStatus.AWAITING_PM_REVIEW


# ---------------------------------------------------------------------------
# mark_pr_created edge cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mark_pr_created_when_docs_not_complete_keeps_status(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.AWAITING_DOCUMENTATION
    task.assigned_to = task_setup["agent_id"]
    task.docs_complete = False
    await db_session.flush()
    pr_num = 10
    out = await svc.mark_pr_created(task.id, pr_number=pr_num, pr_url="u10")
    assert out is not None
    # PR set, but stays in awaiting_documentation
    assert out.pr_number == pr_num
    assert out.status == TaskStatus.AWAITING_DOCUMENTATION


# ---------------------------------------------------------------------------
# complete — happy path & edge cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_complete_returns_none_when_invalid_status(
    task_setup: dict,
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))  # PENDING
    out = await svc.complete(task.id, agent_id=task_setup["agent_id"])
    assert out is None


@pytest.mark.asyncio
async def test_complete_returns_none_when_active_descendants(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    parent = await svc.create(_req(task_setup))
    parent.status = TaskStatus.AWAITING_PM_REVIEW
    await db_session.flush()
    child = await svc.create(_req(task_setup, parent_task_id=parent.id))
    await db_session.flush()
    assert child is not None
    out = await svc.complete(parent.id, agent_id=task_setup["agent_id"])
    assert out is None


@pytest.mark.asyncio
async def test_complete_in_progress_for_own_task(
    task_setup: dict, db_session: AsyncSession
) -> None:
    """PM completes their own in_progress task (not awaiting_pm_review)."""
    svc = task_setup["svc"]
    pm_agent = AgentTable(
        id=uuid4(),
        name="PM",
        slug=f"be-pm-{uuid4().hex[:8]}",
        role=AgentRole.CELL_PM,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="pm",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add(pm_agent)
    await db_session.flush()
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.IN_PROGRESS
    task.assigned_to = pm_agent.id
    await db_session.flush()
    out = await svc.complete(task.id, agent_id=pm_agent.id)
    assert out is not None
    assert out.status == TaskStatus.COMPLETED


@pytest.mark.asyncio
async def test_complete_cell_pm_does_not_escalate_to_main_pm(
    task_setup: dict, db_session: AsyncSession
) -> None:
    """#178: cell_pm completing an awaiting_pm_review non-root task
    transitions it to COMPLETED and does NOT reassign to main_pm.

    Pre-#178 the cell_pm branch of ``_apply_complete_approval_chain``
    reassigned every awaiting_pm_review task to main_pm and kept it in
    awaiting_pm_review for a second-tier review — but the gateway's
    ``main_pm_complete`` rejects every non-root task
    (``parent_task_id IS NOT NULL`` → invalid_state), so main_pm had
    no verb to advance it. Result: the leaf was permanently wedged
    (observed end-to-end this session). The fix removes the cell_pm
    escalation branch; cell PM now completes non-root tasks directly,
    and cell→main escalation, when intended, uses ``submit_up``.
    """
    svc = task_setup["svc"]
    cell_pm = AgentTable(
        id=uuid4(),
        name="CellPM",
        slug=f"be-pm-{uuid4().hex[:8]}",
        role=AgentRole.CELL_PM,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="pm",
        capabilities=[],
        permissions={},
        metrics={},
    )
    main_pm = AgentTable(
        id=uuid4(),
        name="MainPM",
        slug=f"main-pm-{uuid4().hex[:8]}",
        role=AgentRole.MAIN_PM,
        team=Team.MAIN_PM,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="pm",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add_all([cell_pm, main_pm])
    await db_session.flush()
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.AWAITING_PM_REVIEW
    task.assigned_to = cell_pm.id
    await db_session.flush()
    out = await svc.complete(task.id, agent_id=cell_pm.id)
    assert out is not None
    assert out.status == TaskStatus.COMPLETED
    assert out.assigned_to != main_pm.id


@pytest.mark.asyncio
async def test_complete_cell_pm_no_main_pm_completes(
    task_setup: dict, db_session: AsyncSession
) -> None:
    """#178: cell_pm completing awaiting_pm_review transitions to
    COMPLETED regardless of whether a Main PM exists. (Pre-#178 this
    test guarded the "no Main PM → escalation returns None → falls
    through to completion" fallback; post-#178 the cell_pm escalation
    branch is gone entirely, so this path is the only path.)
    """
    svc = task_setup["svc"]
    cell_pm = AgentTable(
        id=uuid4(),
        name="CellPM",
        slug=f"be-pm-{uuid4().hex[:8]}",
        role=AgentRole.CELL_PM,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="pm",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add(cell_pm)
    await db_session.flush()
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.AWAITING_PM_REVIEW
    await db_session.flush()
    out = await svc.complete(task.id, agent_id=cell_pm.id)
    assert out is not None
    # No Main PM in scope — escalation returned None, falls through to completion
    assert out.status == TaskStatus.COMPLETED


@pytest.mark.asyncio
async def test_complete_with_force_with_cancelled_succeeds(
    task_setup: dict, db_session: AsyncSession
) -> None:
    """force_with_cancelled allows completion despite cancelled descendants.

    Strip any leaked Main PMs so the Cell PM completion doesn't escalate
    upward and short-circuit the cancelled-descendant code path.
    """
    svc = task_setup["svc"]
    await db_session.execute(
        AgentTable.__table__.update()
        .where(AgentTable.role == AgentRole.MAIN_PM)
        .values(role=AgentRole.SYSTEM)
    )
    pm_agent = AgentTable(
        id=uuid4(),
        name="PM",
        slug=f"be-pm-{uuid4().hex[:8]}",
        role=AgentRole.CELL_PM,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="pm",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add(pm_agent)
    await db_session.flush()
    parent = await svc.create(_req(task_setup))
    parent.status = TaskStatus.AWAITING_PM_REVIEW
    await db_session.flush()
    child = await svc.create(_req(task_setup, parent_task_id=parent.id))
    child.status = TaskStatus.CANCELLED
    await db_session.flush()
    out = await svc.complete(
        parent.id,
        agent_id=pm_agent.id,
        force_with_cancelled=True,
        justification="not needed anymore",
    )
    assert out is not None
    assert out.status == TaskStatus.COMPLETED


@pytest.mark.asyncio
async def test_complete_without_force_with_cancelled_blocks(
    task_setup: dict, db_session: AsyncSession
) -> None:
    """Without force_with_cancelled, cancelled descendants block completion."""
    svc = task_setup["svc"]
    await db_session.execute(
        AgentTable.__table__.update()
        .where(AgentTable.role == AgentRole.MAIN_PM)
        .values(role=AgentRole.SYSTEM)
    )
    pm_agent = AgentTable(
        id=uuid4(),
        name="PM",
        slug=f"be-pm-{uuid4().hex[:8]}",
        role=AgentRole.CELL_PM,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="pm",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add(pm_agent)
    await db_session.flush()
    parent = await svc.create(_req(task_setup))
    parent.status = TaskStatus.AWAITING_PM_REVIEW
    await db_session.flush()
    child = await svc.create(_req(task_setup, parent_task_id=parent.id))
    child.status = TaskStatus.CANCELLED
    await db_session.flush()
    out = await svc.complete(parent.id, agent_id=pm_agent.id)
    assert out is None


# ---------------------------------------------------------------------------
# apply_escalation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_escalation_reassigns_and_blocks(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    target = AgentTable(
        id=uuid4(),
        name="Target",
        slug=f"target-{uuid4().hex[:8]}",
        role=AgentRole.CELL_PM,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="t",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add(target)
    await db_session.flush()
    task = await svc.create(_req(task_setup))
    task.assigned_to = task_setup["agent_id"]
    task.status = TaskStatus.IN_PROGRESS
    await db_session.flush()
    await svc.apply_escalation(
        task=task,
        target_agent_id=target.id,
        escalator_slug="dev-1",
        target_slug="cell-pm",
        reason="external blocker",
    )
    assert task.status == TaskStatus.BLOCKED
    assert task.assigned_to == target.id
    assert task.blocker_raised_by == task_setup["agent_id"]
    assert "[ESCALATED]" in (task.dev_notes or "")


# ---------------------------------------------------------------------------
# escalate / escalate_up_to_role helpers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_escalate_returns_none_for_missing_task(task_setup: dict) -> None:
    svc = task_setup["svc"]
    out = await svc.escalate(task_setup["agent_id"], uuid4(), reason="x")
    assert out is None


@pytest.mark.asyncio
async def test_escalate_returns_none_for_missing_agent(
    task_setup: dict,
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    out = await svc.escalate(uuid4(), task.id, reason="x")
    assert out is None


@pytest.mark.asyncio
async def test_escalate_returns_none_when_target_slug_not_in_db(
    task_setup: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Escalation target slug exists in config but no AgentTable row matches."""
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    # Force a target slug that won't match any AgentTable row
    monkeypatch.setattr(
        "roboco.agents_config.get_escalation_target",
        lambda _slug: "nonexistent-target",
    )
    out = await svc.escalate(task_setup["agent_id"], task.id, reason="x")
    assert out is None


@pytest.mark.asyncio
async def test_escalate_succeeds_when_target_resolves(
    task_setup: dict,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    svc = task_setup["svc"]
    target = AgentTable(
        id=uuid4(),
        name="Target",
        slug=f"esc-target-{uuid4().hex[:8]}",
        role=AgentRole.CELL_PM,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="t",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add(target)
    await db_session.flush()
    task = await svc.create(_req(task_setup))
    task.assigned_to = task_setup["agent_id"]
    task.status = TaskStatus.IN_PROGRESS
    await db_session.flush()
    monkeypatch.setattr(
        "roboco.agents_config.get_escalation_target",
        lambda _slug: target.slug,
    )
    out = await svc.escalate(task_setup["agent_id"], task.id, reason="bug")
    assert out is not None
    assert out.assigned_to == target.id


@pytest.mark.asyncio
async def test_escalate_up_to_role_returns_none_for_missing_task(
    task_setup: dict,
) -> None:
    svc = task_setup["svc"]
    out = await svc.escalate_up_to_role(task_setup["agent_id"], uuid4(), "main_pm", "x")
    assert out is None


@pytest.mark.asyncio
async def test_escalate_up_to_role_returns_none_for_missing_agent(
    task_setup: dict,
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    out = await svc.escalate_up_to_role(uuid4(), task.id, "main_pm", "x")
    assert out is None


@pytest.mark.asyncio
async def test_escalate_up_to_role_succeeds(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    main_pm = AgentTable(
        id=uuid4(),
        name="MainPM",
        slug=f"main-pm-{uuid4().hex[:8]}",
        role=AgentRole.MAIN_PM,
        team=Team.MAIN_PM,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="m",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add(main_pm)
    await db_session.flush()
    task = await svc.create(_req(task_setup))
    task.assigned_to = task_setup["agent_id"]
    task.status = TaskStatus.IN_PROGRESS
    await db_session.flush()
    out = await svc.escalate_up_to_role(
        task_setup["agent_id"], task.id, "main_pm", "needs main pm"
    )
    assert out is not None


@pytest.mark.asyncio
async def test_escalate_up_to_role_returns_none_when_no_target_role(
    task_setup: dict, db_session: AsyncSession
) -> None:
    """Target role has no agents in DB.

    Strip any leaked Main PMs so escalation hits the no-target branch.
    """
    svc = task_setup["svc"]
    await db_session.execute(
        AgentTable.__table__.update()
        .where(AgentTable.role == AgentRole.MAIN_PM)
        .values(role=AgentRole.SYSTEM)
    )
    task = await svc.create(_req(task_setup))
    await db_session.flush()
    out = await svc.escalate_up_to_role(task_setup["agent_id"], task.id, "main_pm", "x")
    # No main_pm in scope; the unknown-role branch would also short-circuit
    assert out is None


# ---------------------------------------------------------------------------
# unblock_with_restore - full snapshot path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unblock_with_restore_with_invalid_pre_block_state(
    task_setup: dict, db_session: AsyncSession
) -> None:
    """An invalid pre_block_state value falls through to legacy unblock."""
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.BLOCKED
    task.pre_block_state = "garbage"
    # Has a branch (was claimed before blocking) so legacy unblock resumes
    # in_progress rather than returning a never-claimed task to pending.
    task.branch_name = "feature/backend/abc12345"
    await db_session.flush()
    out = await svc.unblock_with_restore(
        pm_agent_id=task_setup["agent_id"], task_id=task.id, restore=True
    )
    # Falls through to legacy unblock which transitions to in_progress
    assert out is not None
    assert out.status == TaskStatus.IN_PROGRESS


@pytest.mark.asyncio
async def test_unblock_with_restore_when_status_not_blocked(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.IN_PROGRESS
    task.pre_block_state = TaskStatus.IN_PROGRESS.value
    await db_session.flush()
    out = await svc.unblock_with_restore(
        pm_agent_id=task_setup["agent_id"], task_id=task.id, restore=True
    )
    # status not BLOCKED but pre_block_state is set → returns None
    assert out is None


# ---------------------------------------------------------------------------
# qa_pass and qa_fail with actor mismatch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_qa_pass_logs_actor_mismatch(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    qa_a = AgentTable(
        id=uuid4(),
        name="QA-A",
        slug=f"be-qa-a-{uuid4().hex[:8]}",
        role=AgentRole.QA,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="qa",
        capabilities=[],
        permissions={},
        metrics={},
    )
    qa_b = AgentTable(
        id=uuid4(),
        name="QA-B",
        slug=f"be-qa-b-{uuid4().hex[:8]}",
        role=AgentRole.QA,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="qa",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add_all([qa_a, qa_b])
    await db_session.flush()
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.AWAITING_QA
    task.claimed_by = qa_a.id  # Different from qa_b
    task.pr_number = 1
    task.pr_url = "u"
    await db_session.flush()
    out = await svc.qa_pass(qa_b.id, task.id, "looks ok")
    # Pass succeeds despite mismatch (warning logged)
    assert out is not None


@pytest.mark.asyncio
async def test_qa_fail_appends_issues_and_calls_fail_qa(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    qa_agent = AgentTable(
        id=uuid4(),
        name="QA",
        slug=f"be-qa-{uuid4().hex[:8]}",
        role=AgentRole.QA,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="qa",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add(qa_agent)
    await db_session.flush()
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.AWAITING_QA
    task.claimed_by = qa_agent.id
    await db_session.flush()
    out = await svc.qa_fail(
        qa_agent.id,
        task.id,
        notes="needs work",
        issues=["typo", "missing test"],
    )
    assert out is not None
    assert out.status == TaskStatus.NEEDS_REVISION
    # Issues block was appended to dev_notes
    assert "typo" in (out.dev_notes or "")
    assert "missing test" in (out.dev_notes or "")


@pytest.mark.asyncio
async def test_qa_fail_returns_none_for_missing_task(
    task_setup: dict,
) -> None:
    svc = task_setup["svc"]
    out = await svc.qa_fail(uuid4(), uuid4(), notes="x", issues=[])
    assert out is None


# ---------------------------------------------------------------------------
# pause_for_agent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pause_for_agent_returns_none_when_wrong_assignee(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    other = AgentTable(
        id=uuid4(),
        name="Other",
        slug=f"other-{uuid4().hex[:8]}",
        role=AgentRole.DEVELOPER,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="x",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add(other)
    await db_session.flush()
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.IN_PROGRESS
    task.assigned_to = other.id
    await db_session.flush()
    out = await svc.pause_for_agent(
        agent_id=task_setup["agent_id"], task_id=task.id, agent_role="developer"
    )
    assert out is None


@pytest.mark.asyncio
async def test_pause_for_agent_calls_pause_when_owner(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.IN_PROGRESS
    task.assigned_to = task_setup["agent_id"]
    await db_session.flush()
    out = await svc.pause_for_agent(
        agent_id=task_setup["agent_id"],
        task_id=task.id,
        agent_role="developer",
    )
    assert out is not None
    assert out.status == TaskStatus.PAUSED


# ---------------------------------------------------------------------------
# list_in_progress_for_agent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_in_progress_for_agent(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.IN_PROGRESS
    task.assigned_to = task_setup["agent_id"]
    await db_session.flush()
    rows = await svc.list_in_progress_for_agent(task_setup["agent_id"])
    assert task.id in {t.id for t in rows}


# ---------------------------------------------------------------------------
# create_subtask
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_subtask_requires_parent_task_id(task_setup: dict) -> None:
    svc = task_setup["svc"]
    with pytest.raises(ValueError, match="parent_task_id"):
        await svc.create_subtask(_req(task_setup, parent_task_id=None))


@pytest.mark.asyncio
async def test_create_subtask_with_assignee_uses_pending(
    task_setup: dict,
) -> None:
    # `create_subtask` enforces TASK_AT_CREATE completeness (Task 18, 2026-05-10),
    # so we pass a description that meets the 20-char minimum.
    svc = task_setup["svc"]
    parent = await svc.create(_req(task_setup))
    sub = await svc.create_subtask(
        _req(
            task_setup,
            description="Subtask with explicit description for completeness rule.",
            parent_task_id=parent.id,
            assigned_to=task_setup["agent_id"],
        )
    )
    assert sub.status == TaskStatus.PENDING


@pytest.mark.asyncio
async def test_create_subtask_without_assignee_uses_backlog(
    task_setup: dict,
) -> None:
    # See test above re: completeness rule on description length.
    svc = task_setup["svc"]
    parent = await svc.create(_req(task_setup))
    sub = await svc.create_subtask(
        _req(
            task_setup,
            description="Subtask with explicit description for completeness rule.",
            parent_task_id=parent.id,
            assigned_to=None,
        )
    )
    assert sub.status == TaskStatus.BACKLOG


# ---------------------------------------------------------------------------
# submit_pm_review (gateway alias)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_submit_pm_review_with_notes(
    task_setup: dict,
    db_session: AsyncSession,
) -> None:
    svc = task_setup["svc"]
    cell_pm = AgentTable(
        id=uuid4(),
        name="PM",
        slug=f"be-pm-{uuid4().hex[:8]}",
        role=AgentRole.CELL_PM,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="pm",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add(cell_pm)
    await db_session.flush()
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.IN_PROGRESS
    task.branch_name = "feature/backend/x"
    task.pr_created = True
    task.pr_number = 1
    await db_session.flush()
    out = await svc.submit_pm_review(cell_pm.id, task.id, "ready")
    assert out is not None


# ---------------------------------------------------------------------------
# resolve_agent_id failure path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_agent_id_raises_for_unknown_slug(
    task_setup: dict,
) -> None:
    svc = task_setup["svc"]
    with pytest.raises(NotFoundError):
        await svc.resolve_agent_id("nonexistent-slug")


# ---------------------------------------------------------------------------
# main_pm_agent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_main_pm_agent_returns_seeded(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    main_pm = AgentTable(
        id=uuid4(),
        name="MainPM",
        slug=f"main-pm-{uuid4().hex[:8]}",
        role=AgentRole.MAIN_PM,
        team=Team.MAIN_PM,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="m",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add(main_pm)
    await db_session.flush()
    out = await svc.main_pm_agent()
    assert out is not None
    assert out.role == AgentRole.MAIN_PM


# ---------------------------------------------------------------------------
# get_active_task_for_agent — returns the most recent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_active_task_for_agent_picks_in_progress(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.IN_PROGRESS
    task.assigned_to = task_setup["agent_id"]
    await db_session.flush()
    out = await svc.get_active_task_for_agent(task_setup["agent_id"])
    assert out is not None
    assert out.id == task.id


# ---------------------------------------------------------------------------
# list_paused_for_agent — returns paused tasks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_paused_for_agent_returns_paused_only(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    paused = await svc.create(_req(task_setup))
    paused.status = TaskStatus.PAUSED
    paused.assigned_to = task_setup["agent_id"]
    await db_session.flush()
    rows = await svc.list_paused_for_agent(task_setup["agent_id"])
    assert paused.id in {t.id for t in rows}


# ---------------------------------------------------------------------------
# list_long_running_blocked
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_long_running_blocked_returns_list(
    task_setup: dict,
) -> None:
    svc = task_setup["svc"]
    rows = await svc.list_long_running_blocked(threshold_minutes=1)
    assert isinstance(rows, list)


# ---------------------------------------------------------------------------
# list_strategic_for_board
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_strategic_for_board_excludes_subtasks(
    task_setup: dict, db_session: AsyncSession
) -> None:
    """Only root tasks (no parent) with NON_TECHNICAL nature in PM review."""
    svc = task_setup["svc"]
    parent = await svc.create(_req(task_setup, nature=TaskNature.NON_TECHNICAL))
    parent.status = TaskStatus.AWAITING_PM_REVIEW
    sub = await svc.create(
        _req(
            task_setup,
            nature=TaskNature.NON_TECHNICAL,
            parent_task_id=parent.id,
        )
    )
    sub.status = TaskStatus.AWAITING_PM_REVIEW
    await db_session.flush()
    rows = await svc.list_strategic_for_board()
    ids = {t.id for t in rows}
    assert parent.id in ids
    assert sub.id not in ids


# ---------------------------------------------------------------------------
# emit_task_event coverage — best-effort
# ---------------------------------------------------------------------------


class _BadBus:
    def is_connected(self) -> bool:
        raise RuntimeError("bus dead")


def _bad_bus_factory() -> _BadBus:
    return _BadBus()


@pytest.mark.asyncio
async def test_emit_task_event_swallows_exception(
    task_setup: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If event bus raises, _emit_task_event must log + return without raising."""
    svc = task_setup["svc"]
    monkeypatch.setattr("roboco.services.task.get_event_bus", _bad_bus_factory)
    # No raise:
    await svc._emit_task_event(EventType.TASK_AWAITING_CEO_APPROVAL, uuid4())


@pytest.mark.asyncio
async def test_emit_task_event_publishes_when_connected(
    task_setup: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    svc = task_setup["svc"]
    publish_mock = AsyncMock()

    class _Bus:
        def is_connected(self) -> bool:
            return True

        async def publish(self, event) -> None:
            await publish_mock(event)

    def _bus_factory() -> _Bus:
        return _Bus()

    monkeypatch.setattr("roboco.services.task.get_event_bus", _bus_factory)
    await svc._emit_task_event(
        EventType.TASK_AWAITING_CEO_APPROVAL, uuid4(), {"key": "value"}
    )
    publish_mock.assert_awaited_once()


# ---------------------------------------------------------------------------
# resolve_pm_for_review chain walks up parents
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_pm_for_review_finds_assignee_up_chain(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    pm_agent = AgentTable(
        id=uuid4(),
        name="PM",
        slug=f"be-pm-{uuid4().hex[:8]}",
        role=AgentRole.CELL_PM,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="pm",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add(pm_agent)
    await db_session.flush()
    grandparent = await svc.create(_req(task_setup, assigned_to=pm_agent.id))
    parent = await svc.create(_req(task_setup, parent_task_id=grandparent.id))
    child = await svc.create(_req(task_setup, parent_task_id=parent.id))
    pm_id = await svc._resolve_pm_for_review(child)
    assert pm_id == pm_agent.id


@pytest.mark.asyncio
async def test_resolve_pm_for_review_returns_none_when_root(
    task_setup: dict,
) -> None:
    svc = task_setup["svc"]
    root = await svc.create(_req(task_setup))
    pm_id = await svc._resolve_pm_for_review(root)
    assert pm_id is None


@pytest.mark.asyncio
async def test_resolve_pm_for_review_returns_none_when_parent_chain_unassigned(
    task_setup: dict,
) -> None:
    """Edge case: when no ancestor in the chain has an assignee."""
    svc = task_setup["svc"]
    grand = await svc.create(_req(task_setup))  # Unassigned
    parent = await svc.create(_req(task_setup, parent_task_id=grand.id))  # Unassigned
    child = await svc.create(_req(task_setup, parent_task_id=parent.id))
    pm_id = await svc._resolve_pm_for_review(child)
    assert pm_id is None
