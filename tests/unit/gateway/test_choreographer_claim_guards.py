"""Gate Set A: claim-time guards restored from pre-gateway _helpers.py:124-204.

Predicates ported into Choreographer claim verbs:
- SEQUENCE_ORDER_VIOLATION   (earlier sibling must be terminal)
- ALREADY_ACTIVE              (no claim while in_progress task is open)
- PAUSED_TASKS_EXIST          (no claim while paused tasks exist)
- PM_CANNOT_EXECUTE_CODE      (cell_pm/main_pm cannot claim task_type=code)
- ROLE_TYPED_CLAIM            (developer/qa/documenter cannot cross-claim)

These mirror pre-gateway gates at commit 0c3d15a, file
roboco/mcp/tasks/handlers/_helpers.py lines 124-204 plus
roboco/mcp/tasks/handlers/claim.py:121-180 for the sibling sequence check.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.services.gateway.choreographer import Choreographer, ChoreographerDeps


def _make_deps(**overrides: Any) -> ChoreographerDeps:
    base: dict[str, Any] = {
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
    return ChoreographerDeps(**base)


def _task_svc_with(
    target: MagicMock,
    *,
    role: str = "developer",
    in_progress: list[MagicMock] | None = None,
    paused: list[MagicMock] | None = None,
    siblings: list[MagicMock] | None = None,
) -> AsyncMock:
    """Build a task service mock primed with the active-task and sibling lookups."""
    task_svc = AsyncMock()
    task_svc.get.return_value = target
    task_svc.agent_for.return_value = MagicMock(role=role, team="backend")
    task_svc.list_in_progress_for_agent.return_value = in_progress or []
    task_svc.list_paused_for_agent.return_value = paused or []
    task_svc.get_subtasks.return_value = siblings or []
    return task_svc


# ---------------------------------------------------------------------------
# A.1 SEQUENCE_ORDER_VIOLATION
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_i_will_work_on_blocks_when_earlier_sibling_open() -> None:
    """Sequence=2 cannot be claimed while sequence=1 sibling is still open."""
    agent_id = uuid4()
    parent_id = uuid4()
    target_id = uuid4()
    earlier_id = uuid4()
    target = MagicMock(
        id=target_id,
        status="pending",
        plan=None,
        assigned_to=None,
        parent_task_id=parent_id,
        sequence=2,
        task_type="code",
        team="backend",
    )
    earlier = MagicMock(
        id=earlier_id,
        status="in_progress",
        sequence=1,
        title="Earlier sibling",
    )
    later = MagicMock(
        id=target_id,
        status="pending",
        sequence=2,
    )
    task_svc = _task_svc_with(target, siblings=[earlier, later])
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.i_will_work_on(agent_id, target_id, plan="x")
    body = env.as_dict()
    assert body["error"] == "invalid_state"
    assert "sequence" in body["message"].lower()
    assert str(earlier_id) in body["remediate"]
    task_svc.claim.assert_not_awaited()


@pytest.mark.asyncio
async def test_i_will_work_on_allows_when_earlier_sibling_terminal() -> None:
    """Earlier siblings completed/cancelled do not block."""
    agent_id = uuid4()
    parent_id = uuid4()
    target_id = uuid4()
    target = MagicMock(
        id=target_id,
        status="pending",
        plan={"x": 1},
        assigned_to=None,
        parent_task_id=parent_id,
        sequence=2,
        task_type="code",
        team="backend",
    )
    earlier_done = MagicMock(id=uuid4(), status="completed", sequence=1)
    earlier_cancelled = MagicMock(id=uuid4(), status="cancelled", sequence=0)
    self_row = MagicMock(id=target_id, status="pending", sequence=2)
    task_svc = _task_svc_with(
        target, siblings=[earlier_done, earlier_cancelled, self_row]
    )
    task_svc.claim.return_value = MagicMock(
        id=target_id,
        status="claimed",
        plan={"x": 1},
        assigned_to=agent_id,
        task_type="code",
    )
    task_svc.start.return_value = MagicMock(
        id=target_id, status="in_progress", plan={"x": 1}, assigned_to=agent_id
    )
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.i_will_work_on(agent_id, target_id)
    assert env.error is None
    task_svc.claim.assert_awaited_once_with(target_id, agent_id)


@pytest.mark.asyncio
async def test_root_task_no_sequence_check() -> None:
    """Root tasks (no parent) skip the sequence check entirely."""
    agent_id = uuid4()
    target_id = uuid4()
    target = MagicMock(
        id=target_id,
        status="pending",
        plan={"x": 1},
        assigned_to=None,
        parent_task_id=None,
        sequence=5,
        task_type="code",
        team="backend",
    )
    task_svc = _task_svc_with(target)
    task_svc.claim.return_value = MagicMock(
        id=target_id, status="claimed", plan={"x": 1}, assigned_to=agent_id
    )
    task_svc.start.return_value = MagicMock(
        id=target_id, status="in_progress", plan={"x": 1}, assigned_to=agent_id
    )
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.i_will_work_on(agent_id, target_id)
    assert env.error is None
    # Sequence check should not have queried siblings on a root task
    task_svc.get_subtasks.assert_not_awaited()


# ---------------------------------------------------------------------------
# A.2 ALREADY_ACTIVE
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_i_will_work_on_blocks_when_agent_has_in_progress_task() -> None:
    agent_id = uuid4()
    target_id = uuid4()
    other_id = uuid4()
    target = MagicMock(
        id=target_id,
        status="pending",
        plan=None,
        assigned_to=None,
        parent_task_id=None,
        sequence=0,
        task_type="code",
        team="backend",
    )
    in_progress = MagicMock(id=other_id, status="in_progress")
    task_svc = _task_svc_with(target, in_progress=[in_progress])
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.i_will_work_on(agent_id, target_id, plan="x")
    body = env.as_dict()
    assert body["error"] == "invalid_state"
    assert str(other_id) in body["message"] or str(other_id) in body["remediate"]
    assert "i_am_done" in body["remediate"] or "i_am_idle" in body["remediate"]
    task_svc.claim.assert_not_awaited()


@pytest.mark.asyncio
async def test_i_will_work_on_resumption_does_not_self_block() -> None:
    """Resuming a claimed task already owned must not trigger ALREADY_ACTIVE."""
    agent_id = uuid4()
    task_id = uuid4()
    claimed = MagicMock(
        id=task_id,
        status="claimed",
        plan={"x": 1},
        assigned_to=agent_id,
        parent_task_id=None,
        sequence=0,
        task_type="code",
        team="backend",
        branch_name="feature/backend/abc",
    )
    started = MagicMock(
        id=task_id, status="in_progress", plan={"x": 1}, assigned_to=agent_id
    )
    task_svc = _task_svc_with(target=claimed)
    # Even if there's an in_progress task with the SAME id, that's the resumption itself
    task_svc.list_in_progress_for_agent.return_value = []
    task_svc.start.return_value = started
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.i_will_work_on(agent_id, task_id)
    assert env.error is None
    task_svc.start.assert_awaited_once_with(task_id, agent_id)


# ---------------------------------------------------------------------------
# A.3 PAUSED_TASKS_EXIST
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_i_will_work_on_blocks_when_agent_has_paused_task() -> None:
    agent_id = uuid4()
    target_id = uuid4()
    paused_id = uuid4()
    target = MagicMock(
        id=target_id,
        status="pending",
        plan=None,
        assigned_to=None,
        parent_task_id=None,
        sequence=0,
        task_type="code",
        team="backend",
    )
    paused = MagicMock(id=paused_id, status="paused")
    task_svc = _task_svc_with(target, paused=[paused])
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.i_will_work_on(agent_id, target_id, plan="x")
    body = env.as_dict()
    assert body["error"] == "invalid_state"
    assert str(paused_id) in body["remediate"]
    assert "resume" in body["remediate"].lower()
    task_svc.claim.assert_not_awaited()


# ---------------------------------------------------------------------------
# A.4 PM_CANNOT_EXECUTE_CODE
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cell_pm_cannot_claim_code_task_via_i_will_work_on() -> None:
    pm_id = uuid4()
    task_id = uuid4()
    target = MagicMock(
        id=task_id,
        status="pending",
        plan=None,
        assigned_to=None,
        parent_task_id=None,
        sequence=0,
        task_type="code",
        team="backend",
    )
    task_svc = _task_svc_with(target, role="cell_pm")
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.i_will_work_on(pm_id, task_id, plan="x")
    body = env.as_dict()
    assert body["error"] == "not_authorized"
    assert "PM" in body["message"] or "code" in body["message"].lower()
    assert (
        "delegate" in body["remediate"].lower()
        or "developer" in body["remediate"].lower()
    )
    task_svc.claim.assert_not_awaited()


@pytest.mark.asyncio
async def test_main_pm_cannot_claim_code_task_via_i_will_work_on() -> None:
    pm_id = uuid4()
    task_id = uuid4()
    target = MagicMock(
        id=task_id,
        status="pending",
        plan=None,
        assigned_to=None,
        parent_task_id=None,
        sequence=0,
        task_type="code",
        team="backend",
    )
    task_svc = _task_svc_with(target, role="main_pm")
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.i_will_work_on(pm_id, task_id, plan="x")
    body = env.as_dict()
    assert body["error"] == "not_authorized"


@pytest.mark.asyncio
async def test_cell_pm_cannot_claim_code_task_via_i_will_plan() -> None:
    pm_id = uuid4()
    task_id = uuid4()
    target = MagicMock(
        id=task_id,
        status="pending",
        plan=None,
        assigned_to=None,
        parent_task_id=None,
        sequence=0,
        task_type="code",
        team="backend",
    )
    task_svc = _task_svc_with(target, role="cell_pm")
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.i_will_plan(pm_id, task_id, plan="x")
    body = env.as_dict()
    assert body["error"] == "not_authorized"
    assert "code" in body["message"].lower() or "execute" in body["message"].lower()


@pytest.mark.asyncio
async def test_pm_can_plan_non_code_parent() -> None:
    pm_id = uuid4()
    task_id = uuid4()
    target = MagicMock(
        id=task_id,
        status="pending",
        plan=None,
        assigned_to=None,
        parent_task_id=None,
        sequence=0,
        task_type="planning",
        team="backend",
    )
    claimed = MagicMock(
        id=task_id, status="claimed", plan=None, assigned_to=pm_id, task_type="planning"
    )
    started = MagicMock(
        id=task_id,
        status="in_progress",
        plan={"text": "x"},
        assigned_to=pm_id,
        task_type="planning",
    )
    task_svc = _task_svc_with(target, role="cell_pm")
    task_svc.claim.return_value = claimed
    task_svc.set_plan.return_value = claimed
    task_svc.start.return_value = started
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.i_will_plan(pm_id, task_id, plan="break it down")
    assert env.error is None


# ---------------------------------------------------------------------------
# A.5 ROLE_TYPED_CLAIM (cross-role rejection)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_developer_cannot_claim_qa_status_task() -> None:
    """Dev calling i_will_work_on on awaiting_qa task gets explicit rejection."""
    dev_id = uuid4()
    task_id = uuid4()
    target = MagicMock(
        id=task_id,
        status="awaiting_qa",
        plan={"x": 1},
        assigned_to=None,
        parent_task_id=None,
        sequence=0,
        task_type="code",
        team="backend",
    )
    task_svc = _task_svc_with(target, role="developer")
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.i_will_work_on(dev_id, task_id)
    body = env.as_dict()
    # Pre-existing path returns invalid_state with status complaint
    assert body["error"] == "invalid_state"


@pytest.mark.asyncio
async def test_qa_cannot_claim_code_task_via_claim_review() -> None:
    """QA calling claim_review on non-awaiting_qa task is rejected by status check."""
    qa_id = uuid4()
    task_id = uuid4()
    target = MagicMock(
        id=task_id,
        status="pending",
        plan=None,
        assigned_to=None,
        parent_task_id=None,
        sequence=0,
        task_type="code",
        team="backend",
    )
    task_svc = _task_svc_with(target, role="qa")
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.claim_review(qa_id, task_id)
    body = env.as_dict()
    assert body["error"] == "invalid_state"


@pytest.mark.asyncio
async def test_documenter_cannot_claim_code_task_via_claim_doc_task() -> None:
    """Documenter calling claim_doc_task on non-awaiting-doc task is rejected."""
    doc_id = uuid4()
    task_id = uuid4()
    target = MagicMock(
        id=task_id,
        status="pending",
        plan=None,
        assigned_to=None,
        parent_task_id=None,
        sequence=0,
        task_type="code",
        team="backend",
    )
    task_svc = _task_svc_with(target, role="documenter")
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.claim_doc_task(doc_id, task_id)
    body = env.as_dict()
    assert body["error"] == "invalid_state"


@pytest.mark.asyncio
async def test_non_developer_role_cannot_claim_via_i_will_work_on() -> None:
    """Even if status would allow, a documenter calling i_will_work_on on pending
    code task is blocked by role-typed claim gate."""
    doc_id = uuid4()
    task_id = uuid4()
    target = MagicMock(
        id=task_id,
        status="pending",
        plan=None,
        assigned_to=None,
        parent_task_id=None,
        sequence=0,
        task_type="code",
        team="backend",
    )
    task_svc = _task_svc_with(target, role="documenter")
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.i_will_work_on(doc_id, task_id, plan="x")
    body = env.as_dict()
    # Role-typed claim refuses with not_authorized
    assert body["error"] == "not_authorized"
    task_svc.claim.assert_not_awaited()


# ---------------------------------------------------------------------------
# Claim review (QA) — A.2/A.3 mirror
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_claim_review_blocks_when_qa_has_in_progress_task() -> None:
    qa_id = uuid4()
    task_id = uuid4()
    other_id = uuid4()
    target = MagicMock(
        id=task_id,
        status="awaiting_qa",
        assigned_to=None,
        parent_task_id=None,
        sequence=0,
        task_type="code",
        team="backend",
        work_session_id=uuid4(),
        branch_name="feature/backend/abc",
    )
    in_progress = MagicMock(id=other_id, status="in_progress")
    task_svc = _task_svc_with(target, role="qa", in_progress=[in_progress])
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.claim_review(qa_id, task_id)
    body = env.as_dict()
    assert body["error"] == "invalid_state"
    assert "i_am_done" in body["remediate"] or "i_am_idle" in body["remediate"]
    task_svc.qa_claim.assert_not_awaited()


@pytest.mark.asyncio
async def test_claim_doc_task_blocks_when_documenter_has_paused_task() -> None:
    doc_id = uuid4()
    task_id = uuid4()
    paused_id = uuid4()
    target = MagicMock(
        id=task_id,
        status="awaiting_documentation",
        assigned_to=None,
        parent_task_id=None,
        sequence=0,
        task_type="code",
        team="backend",
        work_session_id=uuid4(),
        branch_name="feature/backend/abc",
    )
    paused = MagicMock(id=paused_id, status="paused")
    task_svc = _task_svc_with(target, role="documenter", paused=[paused])
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.claim_doc_task(doc_id, task_id)
    body = env.as_dict()
    assert body["error"] == "invalid_state"
    assert "resume" in body["remediate"].lower()
    task_svc.doc_claim.assert_not_awaited()
