"""Gate Set A: claim-time guards restored from pre-gateway _helpers.py:124-204.

Predicates ported into Choreographer claim verbs:
- ALREADY_ACTIVE              (no claim while in_progress task is open)
- PAUSED_TASKS_EXIST          (no claim while paused tasks exist)
- PM_CANNOT_EXECUTE_CODE      (cell_pm/main_pm cannot claim task_type=code)
- ROLE_TYPED_CLAIM            (developer/qa/documenter cannot cross-claim)

These mirror pre-gateway gates at commit 0c3d15a, file
roboco/mcp/tasks/handlers/_helpers.py lines 124-204.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.services.gateway.choreographer import Choreographer, ChoreographerDeps

# #172: a developer fresh claim must carry a substantive step checklist.
# Inert on re-entry/error/non-dev paths, so safe to pass everywhere.
_STEPS = [
    {
        "title": "Implement the change",
        "description": (
            "edit the target file, add tests, run them, and stage the "
            "change for commit on the task branch"
        ),
    }
]


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
    # VerbRunner uses task.session.begin_nested() as a savepoint context
    # manager. AsyncMock auto-attributes any access (so hasattr always
    # returns True); we always overwrite session to a MagicMock with the
    # correct async-context-manager protocol.
    task = base["task"]
    task.session = MagicMock()
    task.session.begin_nested = MagicMock(
        return_value=MagicMock(
            __aenter__=AsyncMock(return_value=None),
            __aexit__=AsyncMock(return_value=False),
        )
    )
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
    # C8: default-fresh journal:decision so PM-decision gate passes.
    # Tests that exercise the gate boundary stub their own value.
    # The check matches MagicMock and AsyncMock (the two default sentinel
    # types pytest's unittest.mock leaves on un-stubbed return_values).
    _ldef = base["journal"].latest_decision_at.return_value
    if type(_ldef).__name__ in ("MagicMock", "AsyncMock"):
        base["journal"].latest_decision_at.return_value = datetime.now(UTC)
    return ChoreographerDeps(**base)


def _task_svc_with(
    target: MagicMock,
    *,
    role: str = "developer",
    agent_id: object | None = None,
    lookups: dict[str, list[MagicMock]] | None = None,
) -> AsyncMock:
    """Build a task service mock primed with the active-task and sibling lookups.

    `agent_id` (when supplied) is used as the GatewayAgentView's id so that
    runner-driven calls like ``task.claim(task.id, agent.id)`` line up
    with the test's assert_awaited_with(target_id, agent_id).

    `lookups` carries the optional in_progress / paused / siblings lists
    (defaulting empty). One bag avoids ruff PLR0913 on the helper sig.
    """
    lookups = lookups or {}
    task_svc = AsyncMock()
    task_svc.get.return_value = target
    task_svc.agent_for.return_value = MagicMock(
        id=agent_id, role=role, team="backend", slug=None
    )
    task_svc.list_in_progress_for_agent.return_value = lookups.get("in_progress", [])
    task_svc.list_paused_for_agent.return_value = lookups.get("paused", [])
    task_svc.get_subtasks.return_value = lookups.get("siblings", [])
    return task_svc


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
    task_svc = _task_svc_with(target, lookups={"in_progress": [in_progress]})
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.i_will_work_on(agent_id, target_id, plan="x", steps=_STEPS)
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
    task_svc = _task_svc_with(target=claimed, agent_id=agent_id)
    # Even if there's an in_progress task with the SAME id, that's the resumption itself
    task_svc.list_in_progress_for_agent.return_value = []
    task_svc.start.return_value = started
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.i_will_work_on(agent_id, task_id, steps=_STEPS)
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
    task_svc = _task_svc_with(target, lookups={"paused": [paused]})
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.i_will_work_on(agent_id, target_id, plan="x", steps=_STEPS)
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

    env = await c.i_will_work_on(pm_id, task_id, plan="x", steps=_STEPS)
    body = env.as_dict()
    assert body["error"] == "not_authorized"
    # Spec produces "role 'cell_pm' may not call 'i_will_work_on'".
    assert "cell_pm" in body["message"]
    assert "i_will_work_on" in body["message"]
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

    env = await c.i_will_work_on(pm_id, task_id, plan="x", steps=_STEPS)
    body = env.as_dict()
    assert body["error"] == "not_authorized"


@pytest.mark.asyncio
async def test_cell_pm_can_plan_code_typed_parent_via_i_will_plan() -> None:
    """Rule change (2026-05-08): the PM-cannot-execute-code guard belongs
    on `i_will_work_on` (the EXECUTION verb), not on `i_will_plan` (the
    PLANNING verb). PMs decompose code-typed parent tasks into
    developer-claimable subtasks all the time; that's planning, not
    executing. Pre-fix this rejection deadlocked every code-typed parent
    in the smoke test (see PRE_GATEWAY_LIFECYCLE.md and the 2026-05-08
    audit-log analysis).
    """
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

    env = await c.i_will_plan(
        pm_id,
        task_id,
        plan="Decompose into 2 dev subtasks.",
        rich_plan={
            "approach": (
                "Split the code-typed parent into two developer-claimable "
                "subtasks: one for API implementation, one for test coverage. "
                "Sequenced so the API lands first; QA reviews each PR after "
                "it opens, documentation follows, then complete and submit "
                "up. No cross-cell dependencies for this slice."
            ),
            "sub_tasks": [
                {
                    "title": "API subtask",
                    "description": (
                        "be-dev-1 implements the endpoint with tests, commits "
                        "with the task-id prefix, opens the leaf PR for QA."
                    ),
                },
            ],
        },
    )
    body = env.as_dict()
    # The PM-cannot-execute-code rejection must NOT fire on i_will_plan.
    assert body.get("error") != "not_authorized", (
        f"i_will_plan was rejected for a code-typed parent; envelope: {body}"
    )


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

    env = await c.i_will_plan(
        pm_id,
        task_id,
        plan="break it down",
        rich_plan={
            "approach": (
                "Single-cell decomposition: backend handles the full scope; "
                "no frontend or ux work required for this planning task. "
                "be-dev-1 owns the change end to end; QA reviews after the "
                "PR opens, documentation follows, then be-pm completes and "
                "submits up. Strict sequencing, no cross-cell dependencies."
            ),
            "sub_tasks": [
                {
                    "title": "Backend planning slice",
                    "description": (
                        "scope the change, assign be-dev-1, who implements "
                        "with tests and opens the leaf PR for QA review."
                    ),
                }
            ],
        },
    )
    assert env.error is None


# ---------------------------------------------------------------------------
# A.5 ROLE_TYPED_CLAIM (cross-role rejection)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_developer_cannot_claim_qa_status_task() -> None:
    """Dev calling i_will_work_on on awaiting_qa task gets explicit rejection.

    spec.CLAIM_RULES restricts DEVELOPER to PENDING/NEEDS_REVISION; an
    awaiting_qa task is reserved for QA. spec.can_claim surfaces this as
    not_authorized (status reserved for another role) which the verb
    relays via Envelope.from_decision. Pre-spec the verb body produced
    invalid_state from a custom else-branch; the spec-driven body now
    returns the more accurate not_authorized.
    """
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

    env = await c.i_will_work_on(dev_id, task_id, steps=_STEPS)
    body = env.as_dict()
    assert body["error"] == "not_authorized"
    assert "developer" in body["message"]
    assert "awaiting_qa" in body["message"]


@pytest.mark.asyncio
async def test_qa_cannot_claim_code_task_via_claim_review() -> None:
    """QA calling claim_review on PENDING task is rejected by claim-rules.

    QA's CLAIM_RULES is {AWAITING_QA}. PENDING is owned by dev/pm — so
    ``_check_claim_rules_narrow`` returns ``not_authorized`` (the
    "other_role_owns_status" branch). Pre-spec the verb body's status
    pre-check returned invalid_state; post-migration the spec gate
    drives the rejection kind.
    """
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
        quick_context=None,
    )
    task_svc = _task_svc_with(target, role="qa")
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.claim_review(qa_id, task_id)
    body = env.as_dict()
    assert body["error"] == "not_authorized"


@pytest.mark.asyncio
async def test_documenter_cannot_claim_code_task_via_claim_doc_task() -> None:
    """Documenter calling claim_doc_task on AWAITING_QA task is rejected by claim-rules.

    Documenter's CLAIM_RULES is {PENDING, AWAITING_DOCUMENTATION}. A
    documenter calling claim_doc_task on AWAITING_QA hits the
    "other_role_owns_status" branch and returns ``not_authorized``.
    Pre-spec the verb body returned invalid_state on the status check;
    post-migration the spec gate drives the rejection kind.
    """
    doc_id = uuid4()
    task_id = uuid4()
    target = MagicMock(
        id=task_id,
        status="awaiting_qa",
        plan=None,
        assigned_to=None,
        parent_task_id=None,
        sequence=0,
        task_type="code",
        team="backend",
        quick_context=None,
    )
    task_svc = _task_svc_with(target, role="documenter")
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.claim_doc_task(doc_id, task_id)
    body = env.as_dict()
    assert body["error"] == "not_authorized"


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

    env = await c.i_will_work_on(doc_id, task_id, plan="x", steps=_STEPS)
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
    task_svc = _task_svc_with(target, role="qa", lookups={"in_progress": [in_progress]})
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
    task_svc = _task_svc_with(target, role="documenter", lookups={"paused": [paused]})
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.claim_doc_task(doc_id, task_id)
    body = env.as_dict()
    assert body["error"] == "invalid_state"
    assert "resume" in body["remediate"].lower()
    task_svc.doc_claim.assert_not_awaited()
