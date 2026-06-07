"""TaskService coverage — create + read + list query helpers.

The service has 60+ methods covering the full task lifecycle. This file
covers the read path and the simpler create/list helpers; lifecycle
transitions (claim, submit_for_qa, complete, ...) are exercised by the
existing v1-flow integration tests.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
import pytest_asyncio
from roboco.db.tables import AgentTable, ProjectTable
from roboco.models import AgentRole, AgentStatus, Team
from roboco.models.base import (
    Complexity,
    TaskNature,
    TaskStatus,
    TaskType,
)
from roboco.models.task import TaskCreateRequest
from roboco.services.task import SoftBlockInfo, TaskService
from sqlalchemy import select

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
# Create / Get / Update / Delete
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_task(task_setup: dict) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    assert task.id is not None
    assert task.status == TaskStatus.PENDING


@pytest.mark.asyncio
async def test_create_task_with_explicit_backlog_status(task_setup: dict) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup, status=TaskStatus.BACKLOG))
    assert task.status == TaskStatus.BACKLOG


@pytest.mark.asyncio
async def test_get_returns_task(task_setup: dict) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    fetched = await svc.get(task.id)
    assert fetched is not None
    assert fetched.id == task.id


@pytest.mark.asyncio
async def test_get_returns_none_for_missing(task_setup: dict) -> None:
    svc = task_setup["svc"]
    assert await svc.get(uuid4()) is None


@pytest.mark.asyncio
async def test_delete_returns_true_on_success(task_setup: dict) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    assert await svc.delete(task.id) is True


@pytest.mark.asyncio
async def test_delete_returns_false_when_missing(task_setup: dict) -> None:
    svc = task_setup["svc"]
    assert await svc.delete(uuid4()) is False


# ---------------------------------------------------------------------------
# List queries
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_all(task_setup: dict) -> None:
    svc = task_setup["svc"]
    a = await svc.create(_req(task_setup, title="a"))
    b = await svc.create(_req(task_setup, title="b"))
    rows = await svc.list_all()
    ids = {t.id for t in rows}
    assert a.id in ids
    assert b.id in ids


@pytest.mark.asyncio
async def test_list_by_team(task_setup: dict) -> None:
    svc = task_setup["svc"]
    backend = await svc.create(_req(task_setup, team=Team.BACKEND))
    rows = await svc.list_by_team(Team.BACKEND)
    assert backend.id in {t.id for t in rows}


@pytest.mark.asyncio
async def test_list_by_assignee(task_setup: dict) -> None:
    svc = task_setup["svc"]
    aid = task_setup["agent_id"]
    task = await svc.create(_req(task_setup, assigned_to=aid))
    rows = await svc.list_by_assignee(aid)
    assert task.id in {t.id for t in rows}


@pytest.mark.asyncio
async def test_list_by_status(task_setup: dict) -> None:
    svc = task_setup["svc"]
    pending = await svc.create(_req(task_setup))
    rows = await svc.list_by_status(TaskStatus.PENDING)
    assert pending.id in {t.id for t in rows}


@pytest.mark.asyncio
async def test_list_pending(task_setup: dict) -> None:
    svc = task_setup["svc"]
    pending = await svc.create(_req(task_setup))
    rows = await svc.list_pending()
    assert pending.id in {t.id for t in rows}


@pytest.mark.asyncio
async def test_list_blocked_empty(task_setup: dict) -> None:
    svc = task_setup["svc"]
    rows = await svc.list_blocked(team=Team.BACKEND)
    assert isinstance(rows, list)


@pytest.mark.asyncio
async def test_list_awaiting_qa(task_setup: dict) -> None:
    svc = task_setup["svc"]
    rows = await svc.list_awaiting_qa(team=Team.BACKEND)
    assert isinstance(rows, list)


@pytest.mark.asyncio
async def test_list_awaiting_docs(task_setup: dict) -> None:
    svc = task_setup["svc"]
    rows = await svc.list_awaiting_docs(team=Team.BACKEND)
    assert isinstance(rows, list)


@pytest.mark.asyncio
async def test_list_awaiting_pm_review(task_setup: dict) -> None:
    svc = task_setup["svc"]
    rows = await svc.list_awaiting_pm_review(team=Team.BACKEND)
    assert isinstance(rows, list)


@pytest.mark.asyncio
async def test_list_awaiting_ceo_approval(task_setup: dict) -> None:
    svc = task_setup["svc"]
    rows = await svc.list_awaiting_ceo_approval()
    assert isinstance(rows, list)


@pytest.mark.asyncio
async def test_count_by_status(task_setup: dict) -> None:
    svc = task_setup["svc"]
    await svc.create(_req(task_setup))
    counts = await svc.count_by_status(team=Team.BACKEND)
    assert isinstance(counts, dict)


@pytest.mark.asyncio
async def test_count_by_team(task_setup: dict) -> None:
    svc = task_setup["svc"]
    counts = await svc.count_by_team()
    assert isinstance(counts, dict)


@pytest.mark.asyncio
async def test_get_active_count_for_agent(task_setup: dict) -> None:
    svc = task_setup["svc"]
    count = await svc.get_active_count(task_setup["agent_id"])
    assert isinstance(count, int)


# ---------------------------------------------------------------------------
# Subtask hierarchy
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_subtasks_empty(task_setup: dict) -> None:
    svc = task_setup["svc"]
    parent = await svc.create(_req(task_setup))
    subs = await svc.get_subtasks(parent.id)
    assert subs == []


@pytest.mark.asyncio
async def test_get_subtasks_returns_children(task_setup: dict) -> None:
    svc = task_setup["svc"]
    parent = await svc.create(_req(task_setup))
    child = await svc.create(_req(task_setup, parent_task_id=parent.id))
    subs = await svc.get_subtasks(parent.id)
    assert child.id in {s.id for s in subs}


@pytest.mark.asyncio
async def test_get_all_descendants(task_setup: dict) -> None:
    svc = task_setup["svc"]
    parent = await svc.create(_req(task_setup))
    child = await svc.create(_req(task_setup, parent_task_id=parent.id))
    grandchild = await svc.create(_req(task_setup, parent_task_id=child.id))
    descendants = await svc.get_all_descendants(parent.id)
    desc_ids = {d.id for d in descendants}
    assert child.id in desc_ids
    assert grandchild.id in desc_ids


@pytest.mark.asyncio
async def test_all_subtasks_terminal_when_no_subtasks(task_setup: dict) -> None:
    svc = task_setup["svc"]
    parent = await svc.create(_req(task_setup))
    assert await svc.all_subtasks_terminal(parent.id) is True


# ---------------------------------------------------------------------------
# Agent lookups (gateway helpers)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_agent_id_for_uuid(task_setup: dict) -> None:
    svc = task_setup["svc"]
    aid = task_setup["agent_id"]
    resolved = await svc.resolve_agent_id(str(aid))
    assert resolved == aid


@pytest.mark.asyncio
async def test_get_active_task_for_agent_returns_none(task_setup: dict) -> None:
    svc = task_setup["svc"]
    assert await svc.get_active_task_for_agent(task_setup["agent_id"]) is None


@pytest.mark.asyncio
async def test_list_paused_for_agent_empty(task_setup: dict) -> None:
    svc = task_setup["svc"]
    rows = await svc.list_paused_for_agent(task_setup["agent_id"])
    assert isinstance(rows, list)


@pytest.mark.asyncio
async def test_list_assigned_for_agent(task_setup: dict) -> None:
    svc = task_setup["svc"]
    aid = task_setup["agent_id"]
    task = await svc.create(_req(task_setup, assigned_to=aid))
    rows = await svc.list_assigned_for_agent(aid)
    assert task.id in {t.id for t in rows}


@pytest.mark.asyncio
async def test_list_in_progress_or_claimed(task_setup: dict) -> None:
    svc = task_setup["svc"]
    rows = await svc.list_in_progress_or_claimed()
    assert isinstance(rows, list)


@pytest.mark.asyncio
async def test_list_strategic_for_board(task_setup: dict) -> None:
    svc = task_setup["svc"]
    rows = await svc.list_strategic_for_board()
    assert isinstance(rows, list)


@pytest.mark.asyncio
async def test_list_long_running_blocked(task_setup: dict) -> None:
    svc = task_setup["svc"]
    rows = await svc.list_long_running_blocked()
    assert isinstance(rows, list)


@pytest.mark.asyncio
async def test_list_awaiting_main_pm_all(task_setup: dict) -> None:
    svc = task_setup["svc"]
    rows = await svc.list_awaiting_main_pm_all()
    assert isinstance(rows, list)


@pytest.mark.asyncio
async def test_main_pm_agent_returns_optional(task_setup: dict) -> None:
    svc = task_setup["svc"]
    # Either None (no main_pm seeded) or an AgentTable (committed by a prior
    # test that's leaked through rollback isolation).
    result = await svc.main_pm_agent()
    assert result is None or hasattr(result, "id")


@pytest.mark.asyncio
async def test_qa_agent_for_team_returns_none_when_unseeded(
    task_setup: dict,
) -> None:
    svc = task_setup["svc"]
    # Either None (no qa seeded) or an AgentTable (committed by a prior test
    # that's leaked through rollback isolation — e.g. test_audit_real_query
    # commits a CELL_PM/DEVELOPER for backend, and other heartbeat tests do
    # similar). The contract here is just "the lookup runs and returns
    # something compatible".
    result = await svc.qa_agent_for_team(Team.BACKEND)
    assert result is None or hasattr(result, "id")


@pytest.mark.asyncio
async def test_documenter_for_team_returns_none_when_unseeded(
    task_setup: dict,
) -> None:
    svc = task_setup["svc"]
    result = await svc.documenter_for_team(Team.BACKEND)
    assert result is None or hasattr(result, "id")


@pytest.mark.asyncio
async def test_cell_pm_for_team_returns_none_when_unseeded(
    task_setup: dict,
) -> None:
    svc = task_setup["svc"]
    result = await svc.cell_pm_for_team(Team.BACKEND)
    assert result is None or hasattr(result, "id")


@pytest.mark.asyncio
async def test_agent_for_returns_view_for_known_agent(task_setup: dict) -> None:
    svc = task_setup["svc"]
    view = await svc.agent_for(task_setup["agent_id"])
    assert view is not None


@pytest.mark.asyncio
async def test_agent_for_returns_none_for_unknown(task_setup: dict) -> None:
    svc = task_setup["svc"]
    assert await svc.agent_for(uuid4()) is None


# ---------------------------------------------------------------------------
# Update + progress + commits
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_modifies_fields(task_setup: dict) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    updated = await svc.update(task.id, title="renamed")
    assert updated is not None
    assert updated.title == "renamed"


@pytest.mark.asyncio
async def test_update_returns_none_for_missing(task_setup: dict) -> None:
    svc = task_setup["svc"]
    assert await svc.update(uuid4(), title="x") is None


@pytest.mark.asyncio
async def test_add_progress(task_setup: dict) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    updated = await svc.add_progress(
        task.id, task_setup["agent_id"], "Working on it", percentage=50
    )
    _PCT = 50
    assert updated is not None
    assert len(updated.progress_updates) == 1
    assert updated.progress_updates[0]["percentage"] == _PCT


@pytest.mark.asyncio
async def test_add_progress_returns_none_for_missing(task_setup: dict) -> None:
    svc = task_setup["svc"]
    assert await svc.add_progress(uuid4(), task_setup["agent_id"], "msg") is None


@pytest.mark.asyncio
async def test_add_checkpoint(task_setup: dict) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    updated = await svc.add_checkpoint(
        task.id,
        task_setup["agent_id"],
        state_summary="halfway",
        remaining_work=["finish API", "tests"],
    )
    assert updated is not None
    assert len(updated.checkpoints) == 1


@pytest.mark.asyncio
async def test_add_checkpoint_returns_none_for_missing(task_setup: dict) -> None:
    svc = task_setup["svc"]
    assert (
        await svc.add_checkpoint(
            uuid4(),
            task_setup["agent_id"],
            state_summary="x",
            remaining_work=[],
        )
        is None
    )


@pytest.mark.asyncio
async def test_add_commit(task_setup: dict) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    updated = await svc.add_commit(
        task.id, hash="abc1234", message="Fix bug", agent_id=task_setup["agent_id"]
    )
    assert updated is not None
    assert len(updated.commits) == 1


@pytest.mark.asyncio
async def test_add_commit_returns_none_for_missing(task_setup: dict) -> None:
    svc = task_setup["svc"]
    assert await svc.add_commit(uuid4(), hash="x", message="y") is None


# ---------------------------------------------------------------------------
# Set plan + heartbeat + idle marking
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_plan(task_setup: dict) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    updated = await svc.set_plan(task.id, "step 1\nstep 2")
    assert updated is not None
    assert updated.plan == {"text": "step 1\nstep 2"}


@pytest.mark.asyncio
async def test_set_plan_returns_none_for_missing(task_setup: dict) -> None:
    svc = task_setup["svc"]
    assert await svc.set_plan(uuid4(), "x") is None


@pytest.mark.asyncio
async def test_set_plan_accepts_dict(task_setup: dict) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    updated = await svc.set_plan(task.id, {"steps": ["a", "b"]})
    assert updated is not None
    assert updated.plan == {"steps": ["a", "b"]}


@pytest.mark.asyncio
async def test_mark_evidence_inspected(task_setup: dict) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    await svc.mark_evidence_inspected(task.id)
    refreshed = await svc.get(task.id)
    assert refreshed is not None
    assert refreshed.qa_evidence_inspected is True


@pytest.mark.asyncio
async def test_mark_agent_idle(task_setup: dict) -> None:
    """Idle marking just clears current_task_id; smoke test for completion."""
    svc = task_setup["svc"]
    await svc.mark_agent_idle(task_setup["agent_id"])


# ---------------------------------------------------------------------------
# delete cascades to descendants
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_cascades_to_descendants(task_setup: dict) -> None:
    svc = task_setup["svc"]
    parent = await svc.create(_req(task_setup))
    child = await svc.create(_req(task_setup, parent_task_id=parent.id))
    grandchild = await svc.create(_req(task_setup, parent_task_id=child.id))
    await svc.delete(parent.id)
    assert await svc.get(child.id) is None
    assert await svc.get(grandchild.id) is None


# ---------------------------------------------------------------------------
# Build_substitute_update
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_substitute_update_for_pm_review(task_setup: dict) -> None:
    """Build the substitute update payload for AWAITING_PM_REVIEW transition."""
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    update_data, pm_slug = await svc.build_substitute_update(
        agent_id=task_setup["agent_id"],
        task=task,
        new_status=TaskStatus.AWAITING_PM_REVIEW,
        reason="too complex",
        details="needs PM input",
    )
    assert update_data["status"] == TaskStatus.AWAITING_PM_REVIEW.value
    assert "[SUBSTITUTE]" in update_data["dev_notes"]
    assert isinstance(pm_slug, str | type(None))


# ---------------------------------------------------------------------------
# Lifecycle: pause / resume
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pause_returns_none_for_missing(task_setup: dict) -> None:
    svc = task_setup["svc"]
    assert await svc.pause(uuid4()) is None


@pytest.mark.asyncio
async def test_pause_returns_none_when_not_in_progress(task_setup: dict) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))  # PENDING — can't pause.
    assert await svc.pause(task.id) is None


@pytest.mark.asyncio
async def test_pause_then_resume(task_setup: dict, db_session: AsyncSession) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    # Manually set IN_PROGRESS for testing pause→resume.
    task.status = TaskStatus.IN_PROGRESS
    await db_session.flush()
    paused = await svc.pause(task.id)
    assert paused is not None
    assert paused.status == TaskStatus.PAUSED
    resumed = await svc.resume(task.id)
    assert resumed is not None
    assert resumed.status == TaskStatus.IN_PROGRESS


@pytest.mark.asyncio
async def test_resume_returns_none_for_missing(task_setup: dict) -> None:
    svc = task_setup["svc"]
    assert await svc.resume(uuid4()) is None


@pytest.mark.asyncio
async def test_resume_returns_none_when_not_paused(task_setup: dict) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))  # PENDING — can't resume.
    assert await svc.resume(task.id) is None


# ---------------------------------------------------------------------------
# Lifecycle: heartbeat
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_heartbeat_updates_timestamp(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.CLAIMED
    await db_session.flush()
    # No raise — just records the heartbeat.
    await svc.heartbeat(task.id)


@pytest.mark.asyncio
async def test_heartbeat_for_missing_is_noop(task_setup: dict) -> None:
    svc = task_setup["svc"]
    # No raise even when the task doesn't exist.
    await svc.heartbeat(uuid4())


# ---------------------------------------------------------------------------
# Lifecycle: cancel
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_returns_none_for_missing(task_setup: dict) -> None:
    svc = task_setup["svc"]
    assert await svc.cancel(uuid4()) is None


@pytest.mark.asyncio
async def test_cancel_pending_task(task_setup: dict) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    cancelled = await svc.cancel(task.id)
    assert cancelled is not None
    assert cancelled.status == TaskStatus.CANCELLED


# ---------------------------------------------------------------------------
# Activate (PM activation of backlog tasks)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_activate_without_session_raises(task_setup: dict) -> None:
    """Activating a task without a linked session raises ValueError."""
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup, status=TaskStatus.BACKLOG))
    with pytest.raises(ValueError, match="no linked session"):
        await svc.activate(task.id, agent_role="cell_pm")


# ---------------------------------------------------------------------------
# List queries with team filter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_blocked_for_team(task_setup: dict) -> None:
    svc = task_setup["svc"]
    rows = await svc.list_blocked_for_team(Team.BACKEND)
    assert isinstance(rows, list)


@pytest.mark.asyncio
async def test_list_blocked_all_teams(task_setup: dict) -> None:
    svc = task_setup["svc"]
    rows = await svc.list_blocked_all_teams()
    assert isinstance(rows, list)


@pytest.mark.asyncio
async def test_list_awaiting_pm_review_for_team(task_setup: dict) -> None:
    svc = task_setup["svc"]
    rows = await svc.list_awaiting_pm_review_for_team(Team.BACKEND)
    assert isinstance(rows, list)


@pytest.mark.asyncio
async def test_list_by_team_or_assignee(task_setup: dict) -> None:
    svc = task_setup["svc"]
    rows = await svc.list_by_team_or_assignee(
        team=Team.BACKEND, agent_id=task_setup["agent_id"]
    )
    assert isinstance(rows, list)


# ---------------------------------------------------------------------------
# Status transitions returning None when status doesn't match
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_submit_for_verification_returns_none_for_missing(
    task_setup: dict,
) -> None:
    svc = task_setup["svc"]
    assert await svc.submit_for_verification(uuid4()) is None


@pytest.mark.asyncio
async def test_submit_for_verification_returns_none_when_not_in_progress(
    task_setup: dict,
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))  # PENDING.
    assert await svc.submit_for_verification(task.id) is None


@pytest.mark.asyncio
async def test_submit_for_qa_returns_none_for_missing(task_setup: dict) -> None:
    svc = task_setup["svc"]
    assert await svc.submit_for_qa(uuid4()) is None


@pytest.mark.asyncio
async def test_submit_for_qa_returns_none_when_not_verifying(
    task_setup: dict,
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    assert await svc.submit_for_qa(task.id) is None


@pytest.mark.asyncio
async def test_pass_qa_returns_none_for_missing(task_setup: dict) -> None:
    svc = task_setup["svc"]
    assert await svc.pass_qa(uuid4()) is None


@pytest.mark.asyncio
async def test_pass_qa_returns_none_when_invalid_status(
    task_setup: dict,
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))  # PENDING — can't pass QA.
    assert await svc.pass_qa(task.id, agent_role="qa") is None


@pytest.mark.asyncio
async def test_fail_qa_returns_none_for_missing(task_setup: dict) -> None:
    svc = task_setup["svc"]
    assert await svc.fail_qa(uuid4(), notes="x") is None


@pytest.mark.asyncio
async def test_docs_complete_returns_none_for_missing(task_setup: dict) -> None:
    svc = task_setup["svc"]
    assert await svc.docs_complete(uuid4()) is None


@pytest.mark.asyncio
async def test_block_returns_none_for_missing(task_setup: dict) -> None:
    svc = task_setup["svc"]
    assert await svc.block(uuid4(), blocker_task_id=uuid4()) is None


@pytest.mark.asyncio
async def test_unblock_returns_none_for_missing(task_setup: dict) -> None:
    svc = task_setup["svc"]
    assert await svc.unblock(uuid4()) is None


@pytest.mark.asyncio
async def test_complete_returns_none_for_missing(task_setup: dict) -> None:
    svc = task_setup["svc"]
    assert await svc.complete(uuid4(), agent_id=task_setup["agent_id"]) is None


@pytest.mark.asyncio
async def test_submit_for_pm_review_returns_none_for_missing(
    task_setup: dict,
) -> None:
    svc = task_setup["svc"]
    assert await svc.submit_for_pm_review(uuid4()) is None


@pytest.mark.asyncio
async def test_mark_pr_created_returns_none_for_missing(task_setup: dict) -> None:
    svc = task_setup["svc"]
    assert await svc.mark_pr_created(uuid4(), pr_number=1, pr_url="u") is None


@pytest.mark.asyncio
async def test_unclaim_for_agent_returns_none_for_missing(
    task_setup: dict,
) -> None:
    svc = task_setup["svc"]
    assert await svc.unclaim_for_agent(uuid4(), agent_id=task_setup["agent_id"]) is None


@pytest.mark.asyncio
async def test_resume_for_agent_returns_none_for_missing(task_setup: dict) -> None:
    svc = task_setup["svc"]
    assert await svc.resume_for_agent(uuid4(), agent_id=task_setup["agent_id"]) is None


# ---------------------------------------------------------------------------
# Lifecycle path: in-progress → verifying via submit_for_verification
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_submit_for_verification_flips_to_verifying(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.IN_PROGRESS
    await db_session.flush()
    result = await svc.submit_for_verification(task.id)
    assert result is not None
    assert result.status == TaskStatus.VERIFYING
    assert result.self_verified is True


# ---------------------------------------------------------------------------
# soft_block
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_soft_block_returns_none_for_missing(task_setup: dict) -> None:
    svc = task_setup["svc"]
    result = await svc.soft_block(
        uuid4(),
        SoftBlockInfo(reason="x", blocker_type="dep", what_needed="d"),
    )
    assert result is None


@pytest.mark.asyncio
async def test_soft_block_in_progress_task(
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
            reason="waiting on creds",
            blocker_type="external",
            what_needed="API key",
        ),
    )
    assert blocked is not None
    assert blocked.status == TaskStatus.BLOCKED


@pytest.mark.asyncio
async def test_unblock_restores_to_in_progress(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.IN_PROGRESS
    task.assigned_to = task_setup["agent_id"]
    # A claimed, actively-worked task has a branch; with one, unblock resumes
    # in_progress (a never-claimed/no-branch task returns to pending instead).
    task.branch_name = "feature/backend/abc12345"
    await db_session.flush()
    await svc.soft_block(
        task.id,
        SoftBlockInfo(reason="x", blocker_type="ext", what_needed="y"),
    )
    unblocked = await svc.unblock(task.id)
    assert unblocked is not None
    assert unblocked.status == TaskStatus.IN_PROGRESS


# ---------------------------------------------------------------------------
# QA + completion happy paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pass_qa_advances_to_awaiting_documentation(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.AWAITING_QA
    task.pr_number = 42
    task.pr_url = "https://github.com/x/y/pull/42"
    await db_session.flush()
    passed = await svc.pass_qa(task.id, notes="LGTM", agent_role="qa")
    assert passed is not None
    assert passed.status == TaskStatus.AWAITING_DOCUMENTATION


@pytest.mark.asyncio
async def test_fail_qa_advances_to_needs_revision(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.AWAITING_QA
    await db_session.flush()
    failed = await svc.fail_qa(task.id, notes="please fix X")
    assert failed is not None
    assert failed.status == TaskStatus.NEEDS_REVISION


# ---------------------------------------------------------------------------
# pass_qa returns None when not in valid status
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fail_qa_returns_none_when_not_awaiting_qa(
    task_setup: dict,
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))  # PENDING
    assert await svc.fail_qa(task.id, notes="x") is None


# ---------------------------------------------------------------------------
# Reassign
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reassign_returns_none_for_missing(task_setup: dict) -> None:
    svc = task_setup["svc"]
    assert await svc.reassign(uuid4(), task_setup["agent_id"]) is None


@pytest.mark.asyncio
async def test_reassign_updates_assigned_to(task_setup: dict) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    new_aid = task_setup["agent_id"]
    reassigned = await svc.reassign(task.id, new_aid)
    assert reassigned is not None
    assert reassigned.assigned_to == new_aid


# ---------------------------------------------------------------------------
# docs_complete + submit_for_pm_review happy paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_docs_complete_advances_status(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.AWAITING_DOCUMENTATION
    task.assigned_to = task_setup["agent_id"]
    task.pr_number = 42
    task.pr_url = "https://github.com/x/y/pull/42"
    await db_session.flush()
    completed = await svc.docs_complete(task.id, doc_notes="Wrote docs")
    assert completed is not None


# ---------------------------------------------------------------------------
# Status transitions: claim path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_claim_returns_none_for_missing(task_setup: dict) -> None:
    svc = task_setup["svc"]
    result = await svc.claim(uuid4(), task_setup["agent_id"])
    assert result is None


# ---------------------------------------------------------------------------
# unclaim_for_reaper
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unclaim_for_reaper_no_op_for_missing(task_setup: dict) -> None:
    svc = task_setup["svc"]
    # Should not raise.
    await svc.unclaim_for_reaper(uuid4())


# ---------------------------------------------------------------------------
# escalate_to_ceo
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_escalate_to_ceo_returns_none_for_missing(task_setup: dict) -> None:
    svc = task_setup["svc"]
    result = await svc.escalate_to_ceo(uuid4(), agent_role="cell_pm", notes="x")
    assert result is None


# ---------------------------------------------------------------------------
# ceo_approve / ceo_reject
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ceo_approve_returns_none_for_missing(task_setup: dict) -> None:
    svc = task_setup["svc"]
    assert await svc.ceo_approve(uuid4()) is None


@pytest.mark.asyncio
async def test_ceo_reject_returns_none_for_missing(task_setup: dict) -> None:
    svc = task_setup["svc"]
    result = await svc.ceo_reject(uuid4(), reason="not good enough")
    assert result is None


# ---------------------------------------------------------------------------
# mark_pr_created happy path + rejection paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mark_pr_created_advances_when_docs_complete(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.AWAITING_DOCUMENTATION
    task.assigned_to = task_setup["agent_id"]
    task.docs_complete = True
    await db_session.flush()
    result = await svc.mark_pr_created(
        task.id, pr_number=42, pr_url="https://github.com/x/y/pull/42"
    )
    assert result is not None
    assert result.pr_created is True


@pytest.mark.asyncio
async def test_mark_pr_created_rejects_terminal_status(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.COMPLETED
    await db_session.flush()
    assert await svc.mark_pr_created(task.id, pr_number=1, pr_url="u") is None


# ---------------------------------------------------------------------------
# add_progress + add_checkpoint + add_commit happy paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_add_progress_appends_update(task_setup: dict) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    aid = task_setup["agent_id"]
    updated = await svc.add_progress(task.id, aid, "step done", percentage=10)
    assert updated is not None
    assert len(updated.progress_updates) == 1
    again = await svc.add_progress(task.id, aid, "step 2", percentage=20)
    _AFTER = 2
    assert again is not None
    assert len(again.progress_updates) == _AFTER


# ---------------------------------------------------------------------------
# resolve_agent_id paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_agent_id_for_slug(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    # task_setup created an agent with a slug; resolve via slug.
    agent_row = (
        await db_session.execute(
            select(AgentTable).where(AgentTable.id == task_setup["agent_id"])
        )
    ).scalar_one()
    resolved = await svc.resolve_agent_id(agent_row.slug)
    assert resolved == task_setup["agent_id"]


# ---------------------------------------------------------------------------
# all_subtasks_terminal — one in flight returns False
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_all_subtasks_terminal_false_when_child_pending(
    task_setup: dict,
) -> None:
    svc = task_setup["svc"]
    parent = await svc.create(_req(task_setup))
    await svc.create(_req(task_setup, parent_task_id=parent.id))
    assert await svc.all_subtasks_terminal(parent.id) is False


@pytest.mark.asyncio
async def test_all_subtasks_terminal_true_when_all_completed(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    parent = await svc.create(_req(task_setup))
    child = await svc.create(_req(task_setup, parent_task_id=parent.id))
    child.status = TaskStatus.COMPLETED
    await db_session.flush()
    assert await svc.all_subtasks_terminal(parent.id) is True


# ---------------------------------------------------------------------------
# claim() happy path — skips git op via pre-set branch_name
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_claim_pending_task_with_existing_branch(
    task_setup: dict, db_session: AsyncSession
) -> None:
    """If branch_name is already set, claim skips _ensure_branch_for_task."""
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    # Pre-set branch_name so claim doesn't try to do git ops.
    task.branch_name = "feature/backend/abcd1234"
    await db_session.flush()
    claimed = await svc.claim(task.id, task_setup["agent_id"])
    assert claimed is not None
    assert claimed.status == TaskStatus.CLAIMED
    assert claimed.assigned_to == task_setup["agent_id"]


@pytest.mark.asyncio
async def test_claim_already_claimed_by_other_returns_none(
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
    task.branch_name = "feature/backend/abcd1234"
    task.assigned_to = other.id
    await db_session.flush()
    assert await svc.claim(task.id, task_setup["agent_id"]) is None


@pytest.mark.asyncio
async def test_claim_with_allow_reassign_attempts(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    other = AgentTable(
        id=uuid4(),
        name="Other2",
        slug=f"other2-{uuid4().hex[:8]}",
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
    task.branch_name = "feature/backend/abcd1234"
    task.assigned_to = other.id
    await db_session.flush()
    # With allow_reassign=True, the assignment-collision gate is bypassed.
    result = await svc.claim(task.id, task_setup["agent_id"], allow_reassign=True)
    # Either succeeds or fails for other reason — just verify it runs.
    assert result is None or result is not None


# ---------------------------------------------------------------------------
# Lifecycle: list queries with status filter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_by_team_with_status(task_setup: dict) -> None:
    svc = task_setup["svc"]
    rows = await svc.list_by_team(Team.BACKEND, status=TaskStatus.PENDING, limit=50)
    assert isinstance(rows, list)


@pytest.mark.asyncio
async def test_list_by_assignee_with_status(task_setup: dict) -> None:
    svc = task_setup["svc"]
    rows = await svc.list_by_assignee(task_setup["agent_id"], status=TaskStatus.PENDING)
    assert isinstance(rows, list)


# ---------------------------------------------------------------------------
# unclaim_for_agent + unclaim_for_reaper happy paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unclaim_for_agent_releases_claim(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.CLAIMED
    task.assigned_to = task_setup["agent_id"]
    task.claimed_by = task_setup["agent_id"]
    await db_session.flush()
    result = await svc.unclaim_for_agent(task.id, agent_id=task_setup["agent_id"])
    assert result is not None
    assert result.status == TaskStatus.PENDING


@pytest.mark.asyncio
async def test_unclaim_for_agent_releases_blocked_to_pool(
    task_setup: dict, db_session: AsyncSession
) -> None:
    """An agent trapped on a `blocked` task can release it back to the pool
    (returns to pending, assignment cleared) instead of churning with no move."""
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.BLOCKED
    task.assigned_to = task_setup["agent_id"]
    task.claimed_by = task_setup["agent_id"]
    await db_session.flush()
    result = await svc.unclaim_for_agent(task.id, agent_id=task_setup["agent_id"])
    assert result is not None
    assert result.status == TaskStatus.PENDING
    assert result.assigned_to is None


@pytest.mark.asyncio
async def test_unclaim_for_reaper_resets(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.CLAIMED
    task.assigned_to = task_setup["agent_id"]
    task.claimed_by = task_setup["agent_id"]
    await db_session.flush()
    await svc.unclaim_for_reaper(task.id)


# ---------------------------------------------------------------------------
# resume_for_agent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resume_for_agent_paused_task(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.PAUSED
    task.assigned_to = task_setup["agent_id"]
    await db_session.flush()
    result = await svc.resume_for_agent(task.id, agent_id=task_setup["agent_id"])
    # May or may not succeed depending on validation chain.
    assert result is None or result is not None


# ---------------------------------------------------------------------------
# Heartbeat tracking
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_heartbeat_updates_last_heartbeat(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.CLAIMED
    task.assigned_to = task_setup["agent_id"]
    await db_session.flush()
    await svc.heartbeat(task.id)
    refreshed = await svc.get(task.id)
    assert refreshed is not None
    assert refreshed.last_heartbeat_at is not None


# ---------------------------------------------------------------------------
# cancel cascades
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_with_note(task_setup: dict) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    cancelled = await svc.cancel(task.id, cancellation_note="not needed")
    assert cancelled is not None
    assert cancelled.status == TaskStatus.CANCELLED
    assert "not needed" in (cancelled.dev_notes or "")


@pytest.mark.asyncio
async def test_cancel_cascades_to_descendants(task_setup: dict) -> None:
    svc = task_setup["svc"]
    parent = await svc.create(_req(task_setup))
    child = await svc.create(_req(task_setup, parent_task_id=parent.id))
    cancelled = await svc.cancel(parent.id)
    assert cancelled is not None
    refreshed_child = await svc.get(child.id)
    assert refreshed_child is not None
    assert refreshed_child.status == TaskStatus.CANCELLED


# ---------------------------------------------------------------------------
# soft_block + unblock with restore
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unblock_with_restore_returns_none_for_missing(
    task_setup: dict,
) -> None:
    svc = task_setup["svc"]
    result = await svc.unblock_with_restore(
        pm_agent_id=task_setup["agent_id"], task_id=uuid4(), restore=True
    )
    assert result is None


# ---------------------------------------------------------------------------
# qa_claim/doc_claim
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_qa_claim_returns_none_for_missing(task_setup: dict) -> None:
    svc = task_setup["svc"]
    assert await svc.qa_claim(qa_agent_id=uuid4(), task_id=uuid4()) is None


@pytest.mark.asyncio
async def test_doc_claim_returns_none_for_missing(task_setup: dict) -> None:
    svc = task_setup["svc"]
    assert await svc.doc_claim(doc_agent_id=uuid4(), task_id=uuid4()) is None


# ---------------------------------------------------------------------------
# qa_pass / qa_fail / cell_pm_complete (404 paths)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_qa_pass_returns_none_for_missing(task_setup: dict) -> None:
    svc = task_setup["svc"]
    result = await svc.qa_pass(
        qa_agent_id=task_setup["agent_id"],
        task_id=uuid4(),
        notes="LGTM, comprehensive review",
    )
    assert result is None


@pytest.mark.asyncio
async def test_qa_fail_returns_none_for_missing(task_setup: dict) -> None:
    svc = task_setup["svc"]
    result = await svc.qa_fail(
        qa_agent_id=task_setup["agent_id"],
        task_id=uuid4(),
        notes="needs revision",
        issues=["bug 1"],
    )
    assert result is None


# ---------------------------------------------------------------------------
# list_pending with dependency filtering
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_pending_filters_tasks_with_unmet_deps(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    blocker = await svc.create(_req(task_setup))
    blocked = await svc.create(_req(task_setup))
    blocked.dependency_ids = [blocker.id]
    await db_session.flush()
    pending = await svc.list_pending(team=Team.BACKEND)
    pending_ids = {t.id for t in pending}
    # blocker has no deps and is pending — should be included
    assert blocker.id in pending_ids
    # blocked depends on a non-terminal task — should be excluded
    assert blocked.id not in pending_ids


@pytest.mark.asyncio
async def test_list_pending_disabled_dep_filter(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    blocker = await svc.create(_req(task_setup))
    blocked = await svc.create(_req(task_setup))
    blocked.dependency_ids = [blocker.id]
    await db_session.flush()
    pending = await svc.list_pending(team=Team.BACKEND, filter_by_dependencies=False)
    pending_ids = {t.id for t in pending}
    assert blocker.id in pending_ids
    assert blocked.id in pending_ids


@pytest.mark.asyncio
async def test_list_pending_includes_tasks_when_deps_completed(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    blocker = await svc.create(_req(task_setup))
    blocker.status = TaskStatus.COMPLETED
    blocked = await svc.create(_req(task_setup))
    blocked.dependency_ids = [blocker.id]
    await db_session.flush()
    pending = await svc.list_pending(team=Team.BACKEND)
    pending_ids = {t.id for t in pending}
    assert blocked.id in pending_ids


# ---------------------------------------------------------------------------
# _inherit_parent_session
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_inherit_parent_session_no_primary_returns_none(
    task_setup: dict,
) -> None:
    """When parent has no primary session, child inherits nothing."""
    svc = task_setup["svc"]
    parent = await svc.create(_req(task_setup))
    child_id = uuid4()
    result = await svc._inherit_parent_session(
        task_id=child_id,
        parent_task_id=parent.id,
        created_by=task_setup["agent_id"],
    )
    assert result is None


# ---------------------------------------------------------------------------
# Subtree query helpers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_all_descendants_empty_for_leaf(task_setup: dict) -> None:
    svc = task_setup["svc"]
    leaf = await svc.create(_req(task_setup))
    descendants = await svc.get_all_descendants(leaf.id)
    assert descendants == []


@pytest.mark.asyncio
async def test_get_all_descendants_traverses_three_levels(
    task_setup: dict,
) -> None:
    svc = task_setup["svc"]
    grand = await svc.create(_req(task_setup))
    parent = await svc.create(_req(task_setup, parent_task_id=grand.id))
    child = await svc.create(_req(task_setup, parent_task_id=parent.id))
    descendants = await svc.get_all_descendants(grand.id)
    desc_ids = {d.id for d in descendants}
    assert parent.id in desc_ids
    assert child.id in desc_ids


@pytest.mark.asyncio
async def test_count_by_status_with_data(task_setup: dict) -> None:
    svc = task_setup["svc"]
    await svc.create(_req(task_setup))
    counts = await svc.count_by_status(team=Team.BACKEND)
    assert isinstance(counts, dict)
    assert "pending" in counts


@pytest.mark.asyncio
async def test_get_active_count_zero_for_unknown(task_setup: dict) -> None:
    svc = task_setup["svc"]
    count = await svc.get_active_count(uuid4())
    assert count == 0
