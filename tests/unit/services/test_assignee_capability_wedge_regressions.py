"""Regression tests for the assignee-capability wedge shapes.

* 4be7d6d3: unblock restored ``awaiting_pm_review`` to an owner whose role has no
  ``complete`` verb, so the named next verb dead-ended in ``not_authorized``.
  The restore must divert to the pool for a role-matched reclaim instead.
* 54e30535: a task was assigned to a role with no verbs for its status. Every
  assignment path (delegate / reassign / admin set) must refuse it with the
  missing capability named.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from roboco.foundation.identity import AGENTS
from roboco.models.base import TaskStatus, Team
from roboco.services.base import AssigneeCapabilityError
from roboco.services.gateway.choreographer import Choreographer, ChoreographerDeps
from roboco.services.task import TaskService


def _svc() -> TaskService:
    session = MagicMock()
    session.flush = AsyncMock()
    session.get = AsyncMock(return_value=None)
    return TaskService(session)


def _task(status: TaskStatus, assignee_slug: str | None = None) -> MagicMock:
    owner = AGENTS[assignee_slug].uuid if assignee_slug else None
    return MagicMock(
        id=uuid4(),
        status=status,
        assigned_to=owner,
        claimed_by=owner,
        active_claimant_id=None,
        branch_name="b",
        dev_notes="",
        team=Team.BACKEND,
    )


def _blocked(pre_state: str, owner_slug: str) -> MagicMock:
    task = _task(TaskStatus.BLOCKED, owner_slug)
    task.pre_block_state = pre_state
    task.pre_block_assignee = AGENTS[owner_slug].uuid
    return task


# --- 4be7d6d3: restore path ------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("pre_state", "owner_slug"),
    [
        ("awaiting_pm_review", "be-dev-1"),
        ("awaiting_pm_review", "be-qa"),
        ("awaiting_qa", "be-dev-2"),
    ],
)
async def test_restore_to_status_owner_cannot_act_on_diverts_to_pool(
    pre_state: str, owner_slug: str
) -> None:
    svc = _svc()
    task = _blocked(pre_state, owner_slug)
    object.__setattr__(svc, "get", AsyncMock(return_value=task))
    notify = AsyncMock()
    object.__setattr__(svc, "_notify_unblock", notify)

    await svc.unblock_with_restore(uuid4(), task.id, restore=True)

    # Status kept (no lifecycle restart, no redone work); owner + claim dropped
    # so the status's own role queue can claim it.
    assert task.status == TaskStatus(pre_state)
    assert task.assigned_to is None
    assert task.claimed_by is None
    assert task.active_claimant_id is None
    assert task.pre_block_state is None
    assert task.pre_block_assignee is None
    assert "[RESTORE REDIRECTED]" in task.dev_notes
    assert pre_state in task.dev_notes
    notify.assert_not_awaited()


@pytest.mark.asyncio
async def test_restore_to_actable_status_keeps_owner() -> None:
    svc = _svc()
    task = _blocked("awaiting_pm_review", "main-pm")
    object.__setattr__(svc, "get", AsyncMock(return_value=task))
    object.__setattr__(svc, "_notify_unblock", AsyncMock())

    await svc.unblock_with_restore(uuid4(), task.id, restore=True)

    assert task.status == TaskStatus.AWAITING_PM_REVIEW
    assert task.assigned_to == AGENTS["main-pm"].uuid
    assert "[RESTORE REDIRECTED]" not in task.dev_notes


# --- 54e30535: assignment paths --------------------------------------------


@pytest.mark.asyncio
async def test_admin_set_refuses_incapable_assignee_naming_capability() -> None:
    svc = _svc()
    task = _task(TaskStatus.AWAITING_QA, "be-qa")
    object.__setattr__(svc, "get", AsyncMock(return_value=task))

    with pytest.raises(AssigneeCapabilityError) as excinfo:
        await svc.update(task.id, assigned_to=AGENTS["be-dev-1"].uuid)

    assert "ASSIGNEE_INCAPABLE" in excinfo.value.message
    assert "developer" in excinfo.value.message
    assert "awaiting_qa" in excinfo.value.message
    assert task.assigned_to == AGENTS["be-qa"].uuid  # no partial write


@pytest.mark.asyncio
async def test_reassign_refuses_incapable_assignee_naming_capability() -> None:
    svc = _svc()
    task = _task(TaskStatus.PENDING, "be-dev-1")
    new_owner = AGENTS["be-qa"].uuid
    object.__setattr__(svc, "get", AsyncMock(return_value=task))
    # Isolate the capability backstop from the pool-divert / cell-PM redirect
    # helpers that run ahead of it (they need a live DB session).
    for helper in (
        "_maybe_divert_board_advisory_reassign",
        "_maybe_divert_main_pm_code_reassign",
    ):
        object.__setattr__(svc, helper, AsyncMock(return_value=None))
    object.__setattr__(
        svc,
        "_resolve_cell_pm_redirect",
        AsyncMock(
            return_value=MagicMock(effective_assignee=new_owner, redirected=False)
        ),
    )

    with pytest.raises(AssigneeCapabilityError) as excinfo:
        await svc.reassign(task.id, AGENTS["be-qa"].uuid)

    assert "qa" in excinfo.value.message
    assert "pending" in excinfo.value.message
    assert task.assigned_to == AGENTS["be-dev-1"].uuid


@pytest.mark.asyncio
async def test_delegate_refuses_incapable_assignee_before_creating() -> None:
    svc = _svc()
    create = AsyncMock()
    object.__setattr__(svc, "create", create)
    req = MagicMock(
        assigned_to=AGENTS["be-qa"].uuid,
        parent_task_id=uuid4(),
        status=None,
        dependency_ids=[],
    )
    ok = MagicMock(passed=True)

    with (
        patch("roboco.foundation.policy.task_completeness.check", return_value=ok),
        patch("roboco.services.task.TaskCreateRequest", MagicMock()) as prepared_cls,
    ):
        prepared_cls.return_value = MagicMock(
            status=TaskStatus.PENDING, assigned_to=AGENTS["be-qa"].uuid
        )
        with pytest.raises(AssigneeCapabilityError) as excinfo:
            await svc.create_subtask(req)

    assert "qa" in excinfo.value.message
    assert "pending" in excinfo.value.message
    create.assert_not_awaited()


# --- choreographer level: the unblock verb after a divert --------------------


def _choreo_deps(task_svc: AsyncMock) -> ChoreographerDeps:
    journal = AsyncMock()
    journal.has_decision_for_task.return_value = True
    journal.has_reflect_for_task.return_value = True
    journal.latest_decision_at.return_value = datetime.now(UTC)
    evidence_repo = AsyncMock()
    for method in (
        "list_unread_a2a",
        "list_unread_mentions",
        "list_pending_notifications",
        "task_metadata_gaps",
        "recent_team_activity",
        "blockers_in_lane",
        "journal_highlights_for_task",
    ):
        getattr(evidence_repo, method).return_value = []
    task_svc.session.execute = AsyncMock(
        return_value=MagicMock(
            scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))
        )
    )
    task_svc.session.begin_nested = MagicMock(
        return_value=MagicMock(
            __aenter__=AsyncMock(return_value=None),
            __aexit__=AsyncMock(return_value=False),
        )
    )
    return ChoreographerDeps(
        task=task_svc,
        work_session=AsyncMock(),
        git=AsyncMock(),
        a2a=AsyncMock(),
        journal=journal,
        audit=AsyncMock(),
        evidence_repo=evidence_repo,
    )


async def _unblock_incapable_owner() -> tuple[Choreographer, MagicMock, dict]:
    """Blocked awaiting_pm_review owned by a developer, run through the verb."""
    svc = _svc()
    task = _blocked("awaiting_pm_review", "be-dev-1")
    task.pr_number = 8
    task.parent_task_id = None
    object.__setattr__(svc, "get", AsyncMock(return_value=task))
    object.__setattr__(svc, "_notify_unblock", AsyncMock())

    task_svc = AsyncMock()
    task_svc.get.return_value = task
    task_svc.agent_for.return_value = MagicMock(role="main_pm", team="backend")
    task_svc.unblock_with_restore.side_effect = svc.unblock_with_restore
    choreo = Choreographer(_choreo_deps(task_svc))
    env = await choreo.unblock(
        AGENTS["main-pm"].uuid, task.id, "block resolved upstream; restoring"
    )
    return choreo, task, env.as_dict()


@pytest.mark.asyncio
async def test_unblock_verb_after_divert_releases_to_pool_without_stale_verb() -> None:
    _, task, body = await _unblock_incapable_owner()

    assert body["error"] is None
    assert body["status"] == "awaiting_pm_review"
    assert task.assigned_to is None
    assert "released to the pool" in body["next"]
    # The old owner's role cannot call complete(); the message must not name it
    # or promise the original assignee will resume.
    assert "complete" not in body["next"]
    assert "will resume" not in body["next"]


@pytest.mark.asyncio
async def test_diverted_task_refuses_old_owner_and_a_capable_pm_completes() -> None:
    choreo, task, _ = await _unblock_incapable_owner()
    dev = AGENTS["be-dev-1"].uuid
    pm = AGENTS["be-pm"].uuid
    task_svc = choreo.task

    # Pre-fix wedge shape: the incapable owner tries the verb the old message
    # named and is refused.
    task_svc.agent_for.return_value = MagicMock(role="developer", team="backend")
    wedge = await choreo.complete(dev, task.id, "completing the diverted task")
    assert wedge.as_dict()["error"] == "not_authorized"
    cell_wedge = await choreo.cell_pm_complete(dev, task.id, "completing it anyway")
    assert cell_wedge.as_dict()["error"] == "not_authorized"

    # A capable PM claims the status's queue entry (assignee set) and completes.
    task.assigned_to = pm
    task.claimed_by = pm
    task.project_id = uuid4()
    task.branch_name = "feature/backend/abc--def"
    task_svc.agent_for.return_value = MagicMock(role="cell_pm", team="backend")
    task_svc.all_subtasks_terminal.return_value = True
    task_svc.cell_pm_complete.return_value = MagicMock(
        id=task.id, status="completed", title="t"
    )
    choreo.git.is_pr_merged_for_task.return_value = True
    ok = await choreo.cell_pm_complete(pm, task.id, "reviewed and approved the work")
    assert ok.as_dict()["error"] is None
    assert ok.status == "completed"
