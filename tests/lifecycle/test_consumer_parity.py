"""Tier 2 - choreographer verb output <-> spec.Decision parity.

For every (role x verb x status x task_type) combo, the choreographer's
envelope must match what spec.can_invoke_intent predicts. This is the
test that makes drift between the spec and the verb body impossible.
"""

from __future__ import annotations

from itertools import product
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.lifecycle import spec
from roboco.services.gateway.choreographer import (
    Choreographer,
    ChoreographerDeps,
)
from roboco.services.gateway.choreographer._impl import DelegateInputs


def _make_deps(task_svc=None) -> ChoreographerDeps:
    base = {
        "task": task_svc or AsyncMock(),
        "work_session": AsyncMock(),
        "git": AsyncMock(),
        "a2a": AsyncMock(),
        "journal": AsyncMock(),
        "audit": AsyncMock(),
        "evidence_repo": AsyncMock(),
    }
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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "role, status, task_type",
    list(
        product(
            [r.value for r in spec.Role if r != spec.Role.AUDITOR],
            [s.value for s in spec.Status],
            ["code"],
        )
    ),
)
async def test_i_will_work_on_matches_spec(
    role: str, status: str, task_type: str
) -> None:
    """Every (role, status, task_type) combo: envelope.error matches spec.Decision.

    Tasks in claimed/in_progress are kept assigned to a DIFFERENT agent so
    the verb's idempotent re-entry / claimed-recovery paths do not bypass
    the spec gate. Those re-entry paths are behavioral concerns the spec
    does not model and are pinned by separate unit tests.
    """
    agent_id = uuid4()
    task_id = uuid4()
    other_agent_id = uuid4()
    task = MagicMock(
        id=task_id,
        status=status,
        task_type=task_type,
        # Always assign to a different agent so the idempotent / recovery
        # branches in the verb body (which intentionally bypass the spec
        # gate) do not fire.
        assigned_to=other_agent_id if status in ("claimed", "in_progress") else None,
        plan="some plan" if status in ("in_progress",) else None,
        commits=[],
        pr_number=None,
        branch_name="feature/x",
        parent_task_id=None,
        sequence=0,
        team="backend",
        title="t",
        quick_context=None,
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = task
    task_svc.agent_for.return_value = MagicMock(
        id=agent_id, role=role, team="backend", slug=None
    )
    task_svc.list_in_progress_for_agent.return_value = []
    task_svc.list_paused_for_agent.return_value = []
    task_svc.get_subtasks.return_value = []
    task_svc.claim.return_value = MagicMock(
        id=task_id, status="claimed", assigned_to=agent_id, plan=None
    )
    task_svc.set_plan.return_value = MagicMock(
        id=task_id, status="claimed", assigned_to=agent_id, plan="my plan"
    )
    task_svc.start.return_value = MagicMock(
        id=task_id,
        status="in_progress",
        assigned_to=agent_id,
        plan="my plan",
        task_type=task_type,
    )
    task_svc.session = MagicMock()
    task_svc.session.begin_nested = MagicMock(
        return_value=MagicMock(
            __aenter__=AsyncMock(return_value=None),
            __aexit__=AsyncMock(return_value=False),
        )
    )
    deps = _make_deps(task_svc=task_svc)
    c = Choreographer(deps)

    ctx = spec.Context(plan="my plan", actor_id=agent_id)
    expected = spec.can_invoke_intent(spec.Role(role), "i_will_work_on", task, ctx)
    # Per-role claim authority (CLAIM_RULES) is now enforced inside
    # spec.can_invoke_action when action == "claim", dispatched by
    # can_invoke_intent. The verb's single gate is can_invoke_intent.
    env = await c.i_will_work_on(agent_id, task_id, plan="my plan")
    body = env.as_dict()
    if expected.allowed:
        # Verb may still fail downstream of the gate (e.g. claim() returns
        # None, runner exception); but the spec gate should not reject.
        assert body["error"] != "not_authorized", (
            f"role={role} status={status} task_type={task_type}: "
            f"spec.can_invoke_intent allowed but envelope returned "
            f"not_authorized: {body}"
        )
    else:
        assert body["error"] == expected.rejection_kind, (
            f"role={role} status={status} task_type={task_type}: "
            f"can_invoke_intent rejected with {expected.rejection_kind}, "
            f"got {body['error']!r}; full body: {body}"
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "role, status, task_type",
    list(
        product(
            [r.value for r in spec.Role if r != spec.Role.AUDITOR],
            [s.value for s in spec.Status],
            ["planning", "code"],
        )
    ),
)
async def test_i_will_plan_matches_spec(role: str, status: str, task_type: str) -> None:
    """Every (role, status, task_type) combo: envelope.error matches spec.Decision.

    Mirror of ``test_i_will_work_on_matches_spec`` for the PM planning verb.
    Tasks in claimed/in_progress are kept assigned to a DIFFERENT agent so
    the verb's idempotent re-entry / claimed-recovery paths do not bypass
    the spec gate. Those re-entry paths are behavioral concerns the spec
    does not model and are pinned by separate unit tests.
    """
    agent_id = uuid4()
    task_id = uuid4()
    other_agent_id = uuid4()
    task = MagicMock(
        id=task_id,
        status=status,
        task_type=task_type,
        # Always assign to a different agent so the idempotent / recovery
        # branches in the verb body (which intentionally bypass the spec
        # gate) do not fire.
        assigned_to=other_agent_id if status in ("claimed", "in_progress") else None,
        plan="some plan" if status in ("in_progress",) else None,
        commits=[],
        pr_number=None,
        branch_name="feature/x",
        parent_task_id=None,
        sequence=0,
        team="backend",
        title="t",
        quick_context=None,
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = task
    task_svc.agent_for.return_value = MagicMock(
        id=agent_id, role=role, team="backend", slug=None
    )
    task_svc.list_in_progress_for_agent.return_value = []
    task_svc.list_paused_for_agent.return_value = []
    task_svc.get_subtasks.return_value = []
    task_svc.claim.return_value = MagicMock(
        id=task_id, status="claimed", assigned_to=agent_id, plan=None
    )
    task_svc.set_plan.return_value = MagicMock(
        id=task_id, status="claimed", assigned_to=agent_id, plan="my plan"
    )
    task_svc.start.return_value = MagicMock(
        id=task_id,
        status="in_progress",
        assigned_to=agent_id,
        plan="my plan",
        task_type=task_type,
    )
    task_svc.session = MagicMock()
    task_svc.session.begin_nested = MagicMock(
        return_value=MagicMock(
            __aenter__=AsyncMock(return_value=None),
            __aexit__=AsyncMock(return_value=False),
        )
    )
    deps = _make_deps(task_svc=task_svc)
    c = Choreographer(deps)

    ctx = spec.Context(plan="my plan", actor_id=agent_id)
    expected = spec.can_invoke_intent(spec.Role(role), "i_will_plan", task, ctx)
    # Per-role claim authority (CLAIM_RULES) is now enforced inside
    # spec.can_invoke_action when action == "claim", dispatched by
    # can_invoke_intent. The verb's single gate is can_invoke_intent.
    env = await c.i_will_plan(agent_id, task_id, plan="my plan")
    body = env.as_dict()
    if expected.allowed:
        # Verb may still fail downstream of the gate (e.g. claim() returns
        # None, runner exception); but the spec gate should not reject.
        assert body["error"] != "not_authorized", (
            f"role={role} status={status} task_type={task_type}: "
            f"spec.can_invoke_intent allowed but envelope returned "
            f"not_authorized: {body}"
        )
    else:
        assert body["error"] == expected.rejection_kind, (
            f"role={role} status={status} task_type={task_type}: "
            f"can_invoke_intent rejected with {expected.rejection_kind}, "
            f"got {body['error']!r}; full body: {body}"
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "role, status",
    list(
        product(
            [r.value for r in spec.Role if r != spec.Role.AUDITOR],
            [s.value for s in spec.Status],
        )
    ),
)
async def test_delegate_matches_spec(role: str, status: str) -> None:
    """Spec parity for delegate's role+state gate.

    delegate composes ``create_subtask`` (PM-only, parent must be
    in_progress). The verb body has additional gates the spec doesn't
    model (delegation chain, assignee-vs-task_type, parent-ownership,
    subtask cap), so this parity test only asserts the spec's role+state
    rejection is correctly mirrored. Chain/assignee/lifecycle-guard
    rejections are pinned by separate unit tests in
    test_choreographer_pm_extras / test_choreographer_delegate_guards.

    Inputs use a valid main_pm -> be-pm planning chain so when the spec
    gate passes, downstream chain/assignee guards pass for main_pm; for
    other roles the spec gate is what rejects.
    """
    pm_id = uuid4()
    parent_id = uuid4()
    project_id = uuid4()
    parent = MagicMock(
        id=parent_id,
        project_id=project_id,
        status=status,
        # Parent owned by the caller so _delegate_lifecycle_guards's
        # ownership check passes when the spec gate allows.
        assigned_to=pm_id,
        title="parent",
        team="backend",
    )
    new_task = MagicMock(id=uuid4())
    task_svc = AsyncMock()
    task_svc.get.return_value = parent
    task_svc.agent_for.return_value = MagicMock(
        id=pm_id, role=role, team="backend", slug=None
    )
    task_svc.get_subtasks.return_value = []
    task_svc.create_subtask.return_value = new_task
    deps = _make_deps(task_svc=task_svc)
    c = Choreographer(deps)

    ctx = spec.Context(actor_id=pm_id)
    expected = spec.can_invoke_intent(spec.Role(role), "delegate", parent, ctx)

    env = await c.delegate(
        pm_id,
        parent_id,
        DelegateInputs(
            title="Backend planning",
            description="Plan backend slice for feature X",
            assigned_to="be-pm",
            team="backend",
            task_type="planning",
        ),
    )
    body = env.as_dict()
    if expected.allowed:
        # Spec allows; chain (main_pm -> be-pm planning) is also valid for
        # role=main_pm. For other PM roles, downstream chain/assignee guards
        # may still reject — but never with the spec's role-only message.
        spec_role_msg = f"role '{role}' may not call 'delegate'"
        assert body.get("message") != spec_role_msg, (
            f"role={role} status={status}: spec.can_invoke_intent allowed "
            f"but envelope surfaced the spec's role-rejection message: {body}"
        )
    else:
        assert body["error"] == expected.rejection_kind, (
            f"role={role} status={status}: can_invoke_intent rejected with "
            f"{expected.rejection_kind}, got {body['error']!r}; full body: {body}"
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "role, status, commits_count, has_pr, owned",
    list(
        product(
            [r.value for r in spec.Role if r != spec.Role.AUDITOR],
            [s.value for s in spec.Status],
            [0, 1],
            [False, True],
            [False, True],
        )
    ),
)
async def test_open_pr_matches_spec(
    role: str, status: str, commits_count: int, has_pr: bool, owned: bool
) -> None:
    """Spec parity for open_pr's role + extra-preconditions gate.

    open_pr's IntentSpec has ``composes=()`` — it's a side-effect-only
    verb (push_branch + create_pr). The spec gate enforces:

      - role in _DEV_ROLES (DEVELOPER only),
      - PRECONDITION_OWNERSHIP (task.assigned_to == ctx.actor_id),
      - PRECONDITION_COMMITS (>=1 commit),
      - PRECONDITION_NO_PR (pr_number is None).

    The verb's idempotent re-entry path (owner + pr_number set returns OK)
    intentionally bypasses the spec gate, since the spec would otherwise
    reject with `tracing_gap` on `no_prior_pr`. That branch is pinned by
    test_choreographer_dev / test_open_pr unit tests; here we exercise
    the non-idempotent combos so the spec gate is the load-bearing check.
    """
    agent_id = uuid4()
    task_id = uuid4()
    other_agent_id = uuid4()
    assigned_to = agent_id if owned else other_agent_id
    # Skip the verb's idempotent shortcut: owner-with-PR returns OK
    # without invoking the spec gate. That's a behavioral concession the
    # spec doesn't model, pinned by separate unit tests.
    if owned and has_pr:
        pytest.skip("idempotent re-entry path bypasses spec gate by design")
    task = MagicMock(
        id=task_id,
        status=status,
        task_type="code",
        assigned_to=assigned_to,
        commits=[{"sha": f"abc{i}"} for i in range(commits_count)],
        pr_number=7 if has_pr else None,
        pr_url="https://gh/x/7" if has_pr else None,
        branch_name="feature/backend/abc12345",
        parent_task_id=None,
        sequence=0,
        team="backend",
        title="t",
        quick_context=None,
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = task
    task_svc.agent_for.return_value = MagicMock(
        id=agent_id, role=role, team="backend", slug=None
    )
    task_svc.session = MagicMock()
    task_svc.session.begin_nested = MagicMock(
        return_value=MagicMock(
            __aenter__=AsyncMock(return_value=None),
            __aexit__=AsyncMock(return_value=False),
        )
    )
    git_svc = AsyncMock()
    git_svc.push_branch.return_value = ("feature/backend/abc12345", 1)
    git_svc.create_pr.return_value = {
        "pr_number": 42,
        "pr_url": "https://gh/x/42",
        "is_root_pr": False,
    }
    deps = _make_deps(task_svc=task_svc)
    deps = ChoreographerDeps(
        task=task_svc,
        work_session=deps.work_session,
        git=git_svc,
        a2a=deps.a2a,
        journal=deps.journal,
        audit=deps.audit,
        evidence_repo=deps.evidence_repo,
    )
    c = Choreographer(deps)

    ctx = spec.Context(actor_id=agent_id)
    expected = spec.can_invoke_intent(spec.Role(role), "open_pr", task, ctx)

    env = await c.open_pr(agent_id, task_id)
    body = env.as_dict()
    if expected.allowed:
        # Spec allows. Verb may still surface a non-error envelope (OK)
        # OR a downstream invalid_state if the runner hits an exception
        # we didn't fully wire in this test mock. The spec gate itself
        # must NOT be the source of any not_authorized / tracing_gap.
        assert body["error"] not in ("not_authorized", "tracing_gap"), (
            f"role={role} status={status} commits={commits_count} "
            f"has_pr={has_pr} owned={owned}: spec.can_invoke_intent "
            f"allowed but envelope rejected at the spec layer: {body}"
        )
    else:
        assert body["error"] == expected.rejection_kind, (
            f"role={role} status={status} commits={commits_count} "
            f"has_pr={has_pr} owned={owned}: can_invoke_intent rejected "
            f"with {expected.rejection_kind}, got {body['error']!r}; "
            f"full body: {body}"
        )
