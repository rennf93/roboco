"""Tier 2 - choreographer verb output <-> spec.Decision parity.

For every (role x verb x status x task_type) combo, the choreographer's
envelope must match what spec.can_invoke_intent predicts. This is the
test that makes drift between the spec and the verb body impossible.
"""

from __future__ import annotations

from datetime import UTC, datetime
from itertools import product
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.foundation.policy import lifecycle as spec
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
    # C8: default-fresh journal:decision so PM-decision gate passes.
    # Parity tests that exercise the gate boundary stub their own value.
    base["journal"].latest_decision_at.return_value = datetime.now(UTC)
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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "role, status, commits_count, owned",
    list(
        product(
            [r.value for r in spec.Role if r != spec.Role.AUDITOR],
            [s.value for s in spec.Status],
            [0, 1],
            [False, True],
        )
    ),
)
async def test_i_am_done_matches_spec(
    role: str, status: str, commits_count: int, owned: bool
) -> None:
    """Spec parity for i_am_done's role + extra-preconditions gate.

    i_am_done's IntentSpec composes ``(submit_verification, submit_qa)``
    with ``extra_preconditions=(PRECONDITION_OWNERSHIP, PRECONDITION_COMMITS)``.
    The spec gate enforces:

      - role in _DEV_ROLES (DEVELOPER only),
      - PRECONDITION_OWNERSHIP (task.assigned_to == ctx.actor_id),
      - PRECONDITION_COMMITS (>=1 commit),
      - first composed action submit_verification's source_status (IN_PROGRESS).

    The verb's recovery branch (owner + status==verifying runs submit_qa
    directly, bypassing the spec gate) intentionally short-circuits — the
    spec doesn't model partial-progress recovery. We skip that single combo
    so the parity check is honest about what the spec gate evaluates.
    """
    agent_id = uuid4()
    task_id = uuid4()
    other_agent_id = uuid4()
    assigned_to = agent_id if owned else other_agent_id
    # Skip the verb's recovery shortcut: owner-with-status==verifying runs
    # submit_qa directly without invoking the spec gate. Pinned by separate
    # unit tests; here we exercise the non-recovery combos so the spec gate
    # is the load-bearing check.
    if owned and status == "verifying":
        pytest.skip("recovery re-entry path bypasses spec gate by design")
    task = MagicMock(
        id=task_id,
        status=status,
        task_type="code",
        assigned_to=assigned_to,
        commits=[{"sha": f"abc{i}"} for i in range(commits_count)],
        pr_number=7,
        pr_url="https://gh/x/7",
        branch_name="feature/backend/abc12345",
        parent_task_id=None,
        sequence=0,
        team="backend",
        title="t",
        quick_context=None,
        # Tracing-gate fields satisfied so a downstream tracing_gap doesn't
        # mask the spec-layer outcome on the allowed branch.
        plan={"x": 1},
        progress_updates=[{"message": "p"}],
        acceptance_criteria=[],
        acceptance_criteria_status=[],
        documents=[],
        dev_notes="",
        work_session_id=None,
        self_verified=False,
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = task
    task_svc.agent_for.return_value = MagicMock(
        id=agent_id, role=role, team="backend", slug=None
    )
    task_svc.submit_verification.return_value = MagicMock(
        id=task_id, status="verifying", assigned_to=agent_id
    )
    task_svc.submit_qa.return_value = MagicMock(
        id=task_id,
        status="awaiting_qa",
        assigned_to=None,
        team="backend",
        pr_url="https://gh/x/7",
        work_session_id=None,
    )
    task_svc.qa_agent_for_team.return_value = None
    task_svc.session = MagicMock()
    task_svc.session.begin_nested = MagicMock(
        return_value=MagicMock(
            __aenter__=AsyncMock(return_value=None),
            __aexit__=AsyncMock(return_value=False),
        )
    )
    deps = _make_deps(task_svc=task_svc)
    journal_svc = deps.journal
    journal_svc.has_reflect_for_task.return_value = True
    # JOURNAL_DURING_WORK_AT_LEAST_ONE: ≥1 decision/learning/struggle.
    journal_svc.has_decision_for_task.return_value = True
    journal_svc.latest_decision_at.return_value = datetime.now(UTC)
    journal_svc.has_learning_for_task.return_value = False
    journal_svc.has_struggle_for_task.return_value = False
    work_svc = deps.work_session
    work_svc.files_changed.return_value = []
    c = Choreographer(deps)

    ctx = spec.Context(actor_id=agent_id, notes="done")
    expected = spec.can_invoke_intent(spec.Role(role), "i_am_done", task, ctx)

    env = await c.i_am_done(agent_id, task_id, "done")
    body = env.as_dict()
    if expected.allowed:
        # Spec allows. Verb may still surface a non-error envelope (OK)
        # OR a downstream tracing_gap from defense-in-depth gates (PR/commits/
        # progress) — but the spec gate itself must NOT be the source of any
        # not_authorized rejection.
        assert body["error"] != "not_authorized", (
            f"role={role} status={status} commits={commits_count} "
            f"owned={owned}: spec.can_invoke_intent allowed but envelope "
            f"surfaced not_authorized at the spec layer: {body}"
        )
    else:
        assert body["error"] == expected.rejection_kind, (
            f"role={role} status={status} commits={commits_count} "
            f"owned={owned}: can_invoke_intent rejected with "
            f"{expected.rejection_kind}, got {body['error']!r}; full body: {body}"
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
async def test_i_am_blocked_matches_spec(role: str, status: str) -> None:
    """Spec parity for i_am_blocked's role + state gate.

    i_am_blocked's IntentSpec composes ``(block,)`` with no
    ``extra_preconditions``. The spec gate enforces:

      - role in (_DEV_ROLES | _QA_ROLES | _DOC_ROLES),
      - composed ``block`` action's source_status (IN_PROGRESS only).

    The verb body has no idempotent / recovery short-circuits, so every
    combo flows through the spec gate. The journal:struggle write is a
    side effect outside the lifecycle action and lives in the verb body
    after the spec gate accepts; it does not affect parity outcomes.
    """
    agent_id = uuid4()
    task_id = uuid4()
    task = MagicMock(
        id=task_id,
        status=status,
        task_type="code",
        # Ownership doesn't gate i_am_blocked (no PRECONDITION_OWNERSHIP),
        # but assigning the task to the caller keeps the downstream
        # task_service.escalate mock realistic.
        assigned_to=agent_id,
        commits=[],
        pr_number=None,
        branch_name="feature/x",
        parent_task_id=None,
        sequence=0,
        team="backend",
        title="t",
        quick_context=None,
        pre_block_state=None,
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = task
    task_svc.agent_for.return_value = MagicMock(
        id=agent_id, role=role, team="backend", slug=None
    )
    task_svc.escalate.return_value = MagicMock(
        id=task_id, status="blocked", assigned_to=agent_id
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

    ctx = spec.Context(actor_id=agent_id, notes="external API down")
    expected = spec.can_invoke_intent(spec.Role(role), "i_am_blocked", task, ctx)

    env = await c.i_am_blocked(agent_id, task_id, "external API down")
    body = env.as_dict()
    if expected.allowed:
        # Spec allows. Verb may still surface a non-error envelope (OK)
        # or a downstream invalid_state if the runner hits an exception
        # we didn't fully wire in this test mock. The spec gate itself
        # must NOT be the source of any not_authorized rejection.
        assert body["error"] != "not_authorized", (
            f"role={role} status={status}: spec.can_invoke_intent allowed "
            f"but envelope surfaced not_authorized at the spec layer: {body}"
        )
    else:
        assert body["error"] == expected.rejection_kind, (
            f"role={role} status={status}: can_invoke_intent rejected with "
            f"{expected.rejection_kind}, got {body['error']!r}; full body: {body}"
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
async def test_unclaim_matches_spec(role: str, status: str) -> None:
    """Spec parity for unclaim's role gate.

    unclaim's IntentSpec has ``composes=()`` — no atomic action runs, so
    the spec gate enforces only role membership (no source-status
    constraint). The verb body owns dispatch via
    ``task.unclaim_for_agent``; the service-level guard refuses with
    None when the status isn't claimed/in_progress, surfacing as
    invalid_state from the verb body. That service-layer rejection is
    NOT the spec's concern.

    Tasks are kept ``assigned_to=agent_id`` so the verb's
    reassignment-rejection branch (Task 6 fix in commit a5d358d) does not
    fire — that branch is a Choreographer-level guard the spec doesn't
    model and is pinned by separate unit tests in test_unclaim.py.
    """
    agent_id = uuid4()
    task_id = uuid4()
    task = MagicMock(
        id=task_id,
        status=status,
        task_type="code",
        # Owned by caller so the reassignment-rejection branch does not
        # fire; we want the spec gate to be the only rejector here.
        assigned_to=agent_id,
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
    # Service-level guard: returns the post-unclaim task on success, or
    # None on state drift. For the parity test we always return a stub
    # so the verb body's None-branch (invalid_state) doesn't mask the
    # spec-layer outcome on the allowed branch.
    task_svc.unclaim_for_agent.return_value = MagicMock(
        id=task_id, status="pending", assigned_to=None
    )
    deps = _make_deps(task_svc=task_svc)
    c = Choreographer(deps)

    ctx = spec.Context(actor_id=agent_id)
    expected = spec.can_invoke_intent(spec.Role(role), "unclaim", task, ctx)

    env = await c.unclaim(agent_id, task_id)
    body = env.as_dict()
    if expected.allowed:
        # Spec allows. Verb may still surface a non-error envelope (OK)
        # or a downstream invalid_state if unclaim_for_agent returns
        # None — but the spec gate itself must NOT be the source of
        # any not_authorized rejection.
        assert body["error"] != "not_authorized", (
            f"role={role} status={status}: spec.can_invoke_intent allowed "
            f"but envelope surfaced not_authorized at the spec layer: {body}"
        )
    else:
        assert body["error"] == expected.rejection_kind, (
            f"role={role} status={status}: can_invoke_intent rejected with "
            f"{expected.rejection_kind}, got {body['error']!r}; full body: {body}"
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
async def test_resume_matches_spec(role: str, status: str) -> None:
    """Spec parity for resume's role + state gate.

    resume's IntentSpec composes ``("resume",)``. The spec gate enforces:

      - role in (_DEV_ROLES | _QA_ROLES | _DOC_ROLES | _PM_ROLES),
      - composed ``resume`` action's source_status (PAUSED only).

    Tasks are kept ``assigned_to=agent_id`` so the verb's
    reassignment-rejection branch (Task 6 fix in commit a5d358d) does not
    fire — that branch is a Choreographer-level guard the spec doesn't
    model and is pinned by separate unit tests in test_resume.py.
    """
    agent_id = uuid4()
    task_id = uuid4()
    task = MagicMock(
        id=task_id,
        status=status,
        task_type="code",
        # Owned by caller so the reassignment-rejection branch does not
        # fire; we want the spec gate to be the only rejector here.
        assigned_to=agent_id,
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
    task_svc.resume_for_agent.return_value = MagicMock(
        id=task_id, status="in_progress", assigned_to=agent_id
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

    ctx = spec.Context(actor_id=agent_id)
    expected = spec.can_invoke_intent(spec.Role(role), "resume", task, ctx)

    env = await c.resume(agent_id, task_id)
    body = env.as_dict()
    if expected.allowed:
        # Spec allows. Verb may still surface a non-error envelope (OK)
        # or a downstream invalid_state if the runner hits an exception
        # we didn't fully wire in this test mock. The spec gate itself
        # must NOT be the source of any not_authorized rejection.
        assert body["error"] != "not_authorized", (
            f"role={role} status={status}: spec.can_invoke_intent allowed "
            f"but envelope surfaced not_authorized at the spec layer: {body}"
        )
    else:
        assert body["error"] == expected.rejection_kind, (
            f"role={role} status={status}: can_invoke_intent rejected with "
            f"{expected.rejection_kind}, got {body['error']!r}; full body: {body}"
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
async def test_complete_matches_spec(role: str, status: str) -> None:
    """Spec parity for complete's role + state gate (the dispatcher layer).

    complete's IntentSpec composes ``("complete",)``; the spec gate
    enforces:

      - role in _PM_ROLES (CELL_PM, MAIN_PM),
      - composed ``complete`` action's source_status (AWAITING_PM_REVIEW only).

    The dispatcher routes to ``cell_pm_complete`` / ``main_pm_complete``
    after the gate accepts. Both lower-level methods retain their own
    pre-flight guards (PR mergeability, journal:decision presence,
    subtasks-terminal) — those model preconditions the spec doesn't
    cover, and may emit non-spec rejection kinds (tracing_gap,
    invalid_state). The parity assertion is therefore one-sided: when
    the spec rejects, the envelope MUST surface that exact
    rejection_kind; when the spec allows, the envelope may still be
    rejected by a downstream guard, but the spec gate itself must NOT
    be the source of any not_authorized rejection.

    Tasks are kept ``assigned_to=agent_id`` so the lower-level guards'
    "not assigned to you" branch does not fire on the allowed path.
    """
    agent_id = uuid4()
    task_id = uuid4()
    task = MagicMock(
        id=task_id,
        status=status,
        task_type="code",
        # Owned by caller so the lower-level _*_pm_complete_guard
        # "not assigned to you" branch does not fire on the allowed
        # path; we want the spec gate to be the only rejector here.
        assigned_to=agent_id,
        commits=[],
        pr_number=8,
        branch_name="feature/backend/abc--def",
        # Cell-PM path needs a parent_task_id; main-PM path needs None.
        # Pick parent_task_id=None so main_pm_complete's own non-root
        # guard doesn't fire on cell_pm. cell_pm_complete doesn't
        # require parent_task_id either — _maybe_advance_parent_to_pm_review
        # short-circuits when leaf_parent_id is None.
        parent_task_id=None,
        sequence=0,
        team="backend",
        title="t",
        quick_context=None,
    )
    after = MagicMock(
        id=task_id, status="completed", assigned_to=agent_id, team="backend"
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = task
    task_svc.agent_for.return_value = MagicMock(
        id=agent_id, role=role, team="backend", slug=None
    )
    task_svc.cell_pm_complete.return_value = after
    task_svc.escalate_to_ceo.return_value = MagicMock(
        id=task_id, status="awaiting_ceo_approval", assigned_to=None, team="backend"
    )
    task_svc.all_subtasks_terminal.return_value = True
    git_svc = AsyncMock()
    git_svc.pr_merge.return_value = {"merged": True, "merge_commit_sha": "x"}
    git_svc.create_pr.return_value = {"pr_number": 99, "pr_url": "x"}
    git_svc.pr_target.return_value = "master"
    journal_svc = AsyncMock()
    journal_svc.has_decision_for_task.return_value = True
    journal_svc.latest_decision_at.return_value = datetime.now(UTC)
    deps_kwargs = {
        "task": task_svc,
        "work_session": AsyncMock(),
        "git": git_svc,
        "a2a": AsyncMock(),
        "journal": journal_svc,
        "audit": AsyncMock(),
        "evidence_repo": AsyncMock(),
    }
    repo = deps_kwargs["evidence_repo"]
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
    deps = ChoreographerDeps(**deps_kwargs)
    c = Choreographer(deps)

    ctx = spec.Context(actor_id=agent_id)
    expected = spec.can_invoke_intent(spec.Role(role), "complete", task, ctx)

    env = await c.complete(agent_id, task_id, notes="reviewed and approved")
    body = env.as_dict()
    if expected.allowed:
        # Spec allows. The dispatcher routes to cell_pm_complete or
        # main_pm_complete; downstream guards may still reject (e.g.
        # tracing_gap on missing journal:decision), but the spec gate
        # itself must NOT be the source of any not_authorized rejection.
        assert body["error"] != "not_authorized", (
            f"role={role} status={status}: spec.can_invoke_intent allowed "
            f"but envelope surfaced not_authorized at the spec layer: {body}"
        )
    else:
        assert body["error"] == expected.rejection_kind, (
            f"role={role} status={status}: can_invoke_intent rejected with "
            f"{expected.rejection_kind}, got {body['error']!r}; full body: {body}"
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
async def test_escalate_up_matches_spec(role: str, status: str) -> None:
    """Spec parity for escalate_up's role gate.

    escalate_up's IntentSpec has ``composes=()`` — no atomic action runs,
    so the spec gate enforces only role membership (cell_pm or main_pm),
    no source-status constraint. The verb body owns dispatch via
    ``task.escalate(...)`` and keeps two verb-specific preflight guards
    the spec does not model: ``journal:decision`` presence, and
    ``escalation_target`` configuration on the agent record. Both are
    satisfied here so the spec gate is the load-bearing rejector.
    """
    agent_id = uuid4()
    task_id = uuid4()
    task = MagicMock(
        id=task_id,
        status=status,
        task_type="code",
        assigned_to=agent_id,
        commits=[],
        pr_number=None,
        branch_name="feature/backend/abc",
        parent_task_id=None,
        sequence=0,
        team="backend",
        title="t",
        quick_context=None,
    )
    after = MagicMock(
        id=task_id, status="blocked", assigned_to=agent_id, team="backend"
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = task
    # escalation_target is not on the spec — set it so the verb-specific
    # preflight does not surface a non-spec invalid_state on the allowed
    # branch and mask the spec-layer outcome.
    task_svc.agent_for.return_value = MagicMock(
        id=agent_id, role=role, team="backend", slug=None, escalation_target="main-pm"
    )
    task_svc.escalate.return_value = after
    journal_svc = AsyncMock()
    # journal:decision is not on the spec — satisfy it so the verb-specific
    # preflight does not surface a non-spec tracing_gap on the allowed branch.
    journal_svc.has_decision_for_task.return_value = True
    journal_svc.latest_decision_at.return_value = datetime.now(UTC)
    deps = _make_deps(task_svc=task_svc)
    deps = ChoreographerDeps(
        task=task_svc,
        work_session=deps.work_session,
        git=deps.git,
        a2a=deps.a2a,
        journal=journal_svc,
        audit=deps.audit,
        evidence_repo=deps.evidence_repo,
    )
    c = Choreographer(deps)

    ctx = spec.Context(actor_id=agent_id, notes="please help")
    expected = spec.can_invoke_intent(spec.Role(role), "escalate_up", task, ctx)

    env = await c.escalate_up(agent_id, task_id, reason="please help")
    body = env.as_dict()
    if expected.allowed:
        # Spec allows. Verb-specific preflight (journal:decision +
        # escalation_target) is wired to pass; the verb may still surface
        # OK or a downstream invalid_state if task.escalate returns None,
        # but the spec gate itself must NOT be the source of any
        # not_authorized rejection.
        assert body["error"] != "not_authorized", (
            f"role={role} status={status}: spec.can_invoke_intent allowed "
            f"but envelope surfaced not_authorized at the spec layer: {body}"
        )
    else:
        assert body["error"] == expected.rejection_kind, (
            f"role={role} status={status}: can_invoke_intent rejected with "
            f"{expected.rejection_kind}, got {body['error']!r}; full body: {body}"
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
async def test_escalate_to_ceo_matches_spec(role: str, status: str) -> None:
    """Spec parity for escalate_to_ceo's role + state gate.

    escalate_to_ceo's IntentSpec composes ``("escalate_to_ceo",)``. The
    spec gate enforces:

      - role in {main_pm, product_owner, head_marketing},
      - composed ``escalate_to_ceo`` action's source_status
        (AWAITING_PM_REVIEW only).

    The verb body keeps the journal:decision preflight (the spec doesn't
    model journal side effects); satisfied here so the spec gate is the
    load-bearing rejector. After the runner returns the verb body
    reassigns to None (CEO acts via UI).
    """
    agent_id = uuid4()
    task_id = uuid4()
    task = MagicMock(
        id=task_id,
        status=status,
        task_type="code",
        # Ownership doesn't gate escalate_to_ceo (no PRECONDITION_OWNERSHIP).
        assigned_to=agent_id,
        commits=[],
        pr_number=None,
        branch_name="feature/backend/abc",
        parent_task_id=None,
        sequence=0,
        team="backend",
        title="t",
        quick_context=None,
    )
    after = MagicMock(
        id=task_id,
        status="awaiting_ceo_approval",
        assigned_to=None,
        team="backend",
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = task
    task_svc.agent_for.return_value = MagicMock(
        id=agent_id, role=role, team="backend", slug=None
    )
    task_svc.escalate_to_ceo.return_value = after
    task_svc.session = MagicMock()
    task_svc.session.begin_nested = MagicMock(
        return_value=MagicMock(
            __aenter__=AsyncMock(return_value=None),
            __aexit__=AsyncMock(return_value=False),
        )
    )
    journal_svc = AsyncMock()
    journal_svc.has_decision_for_task.return_value = True
    journal_svc.latest_decision_at.return_value = datetime.now(UTC)
    deps = _make_deps(task_svc=task_svc)
    deps = ChoreographerDeps(
        task=task_svc,
        work_session=deps.work_session,
        git=deps.git,
        a2a=deps.a2a,
        journal=journal_svc,
        audit=deps.audit,
        evidence_repo=deps.evidence_repo,
    )
    c = Choreographer(deps)

    ctx = spec.Context(actor_id=agent_id, notes="ready for CEO sign-off")
    expected = spec.can_invoke_intent(spec.Role(role), "escalate_to_ceo", task, ctx)

    env = await c.escalate_to_ceo(agent_id, task_id, reason="ready for CEO sign-off")
    body = env.as_dict()
    if expected.allowed:
        # Spec allows. Verb may still surface a non-error envelope (OK)
        # or a downstream tracing_gap from journal:decision absence (we
        # wire it to pass); the spec gate itself must NOT be the source
        # of any not_authorized rejection.
        assert body["error"] != "not_authorized", (
            f"role={role} status={status}: spec.can_invoke_intent allowed "
            f"but envelope surfaced not_authorized at the spec layer: {body}"
        )
    else:
        assert body["error"] == expected.rejection_kind, (
            f"role={role} status={status}: can_invoke_intent rejected with "
            f"{expected.rejection_kind}, got {body['error']!r}; full body: {body}"
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
async def test_submit_up_matches_spec(role: str, status: str) -> None:
    """Spec parity for submit_up's role + state gate.

    submit_up's IntentSpec composes ``("submit_pm_review",)`` with side
    effects ``("create_pr",)``. The spec gate enforces:

      - role == cell_pm,
      - composed ``submit_pm_review`` action's source_status
        (IN_PROGRESS only).

    The verb body keeps ``_submit_up_guard`` (ownership + notes-length +
    journal:decision + subtasks-terminal + branch-present). All of those
    are satisfied here so the spec gate is the load-bearing rejector.
    """
    agent_id = uuid4()
    task_id = uuid4()
    task = MagicMock(
        id=task_id,
        status=status,
        task_type="code",
        # Owned by caller so the verb-specific ownership preflight does
        # not surface a non-spec rejection on the allowed branch.
        assigned_to=agent_id,
        commits=[],
        pr_number=None,
        branch_name="feature/backend/abc",
        parent_task_id=None,
        sequence=0,
        team="backend",
        title="t",
        quick_context=None,
    )
    after = MagicMock(
        id=task_id,
        status="awaiting_pm_review",
        assigned_to=agent_id,
        branch_name="feature/backend/abc",
        team="backend",
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = task
    task_svc.agent_for.return_value = MagicMock(
        id=agent_id, role=role, team="backend", slug=None
    )
    task_svc.submit_pm_review.return_value = after
    task_svc.all_subtasks_terminal.return_value = True
    task_svc.main_pm_agent.return_value = MagicMock(id=uuid4())
    task_svc.session = MagicMock()
    task_svc.session.begin_nested = MagicMock(
        return_value=MagicMock(
            __aenter__=AsyncMock(return_value=None),
            __aexit__=AsyncMock(return_value=False),
        )
    )
    git_svc = AsyncMock()
    git_svc.create_pr.return_value = {"pr_number": 12, "pr_url": "x"}
    journal_svc = AsyncMock()
    journal_svc.has_decision_for_task.return_value = True
    journal_svc.latest_decision_at.return_value = datetime.now(UTC)
    deps = _make_deps(task_svc=task_svc)
    deps = ChoreographerDeps(
        task=task_svc,
        work_session=deps.work_session,
        git=git_svc,
        a2a=deps.a2a,
        journal=journal_svc,
        audit=deps.audit,
        evidence_repo=deps.evidence_repo,
    )
    c = Choreographer(deps)

    notes = "cell completed all subtasks; ready for main pm review"
    ctx = spec.Context(actor_id=agent_id, notes=notes)
    expected = spec.can_invoke_intent(spec.Role(role), "submit_up", task, ctx)

    env = await c.submit_up(agent_id, task_id, notes=notes)
    body = env.as_dict()
    if expected.allowed:
        # Spec allows. Verb may still surface a non-error envelope (OK)
        # or a downstream tracing_gap from one of the _submit_up_guard
        # preconditions (which we wire to pass); the spec gate itself
        # must NOT be the source of any not_authorized rejection.
        assert body["error"] != "not_authorized", (
            f"role={role} status={status}: spec.can_invoke_intent allowed "
            f"but envelope surfaced not_authorized at the spec layer: {body}"
        )
    else:
        assert body["error"] == expected.rejection_kind, (
            f"role={role} status={status}: can_invoke_intent rejected with "
            f"{expected.rejection_kind}, got {body['error']!r}; full body: {body}"
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
async def test_claim_review_matches_spec(role: str, status: str) -> None:
    """Spec parity for claim_review's role + claim source-status gate.

    claim_review's IntentSpec composes ``("claim", "start")`` and is
    restricted to QA. The spec gate enforces:

      - role == qa,
      - claim's source_statuses (PENDING / NEEDS_REVISION / AWAITING_QA
        / AWAITING_DOCUMENTATION),
      - CLAIM_RULES narrowing (qa only allowed from PENDING / AWAITING_QA).

    The verb body owns dispatch via ``task.qa_claim`` (not the runner's
    claim+start chain) because the runtime semantic is "QA inspects,
    status stays at awaiting_qa" — see qa.py module docstring. The
    behavioral claim guards (already_active / paused / sibling_sequence
    skipped) run after the spec gate; they're not modelled by the spec.
    """
    agent_id = uuid4()
    task_id = uuid4()
    task = MagicMock(
        id=task_id,
        status=status,
        task_type="code",
        assigned_to=None,
        commits=[],
        pr_number=None,
        branch_name="feature/backend/abc",
        parent_task_id=None,
        sequence=0,
        team="backend",
        title="t",
        quick_context=None,
        work_session_id=None,
    )
    after = MagicMock(
        id=task_id,
        status=status,
        assigned_to=agent_id,
        team="backend",
        branch_name="feature/backend/abc",
        work_session_id=None,
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = task
    task_svc.agent_for.return_value = MagicMock(
        id=agent_id, role=role, team="backend", slug=None
    )
    task_svc.qa_claim.return_value = after
    task_svc.list_in_progress_for_agent.return_value = []
    task_svc.list_paused_for_agent.return_value = []
    deps = _make_deps(task_svc=task_svc)
    c = Choreographer(deps)

    ctx = spec.Context(actor_id=agent_id)
    expected = spec.can_invoke_intent(spec.Role(role), "claim_review", task, ctx)

    env = await c.claim_review(agent_id, task_id)
    body = env.as_dict()
    if expected.allowed:
        # Spec allows. Verb may still surface a non-error envelope (OK)
        # or a downstream behavioral guard rejection; the spec gate itself
        # must NOT be the source of any not_authorized rejection.
        assert body["error"] != "not_authorized", (
            f"role={role} status={status}: spec.can_invoke_intent allowed "
            f"but envelope surfaced not_authorized at the spec layer: {body}"
        )
    else:
        assert body["error"] == expected.rejection_kind, (
            f"role={role} status={status}: can_invoke_intent rejected with "
            f"{expected.rejection_kind}, got {body['error']!r}; full body: {body}"
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
async def test_pass_review_matches_spec(role: str, status: str) -> None:
    """Spec parity for pass_review's role + state gate.

    pass_review's IntentSpec composes ``("qa_pass",)`` and is QA-only.
    The spec gate enforces:

      - role == qa,
      - composed ``qa_pass`` action's source_status (AWAITING_QA),
      - self-review block (qa_pass.self_review_block=True; the verb body
        builds a Context with actor_slug + original_developer_slug so the
        spec naturally rejects self-review).

    The verb-specific gates (notes-length / journal:learning /
    qa_evidence_inspected) live in the verb body — none are modelled by
    the spec. They're wired to pass here so the spec gate is the load-
    bearing rejector. ``_verify_qa_owner`` runs FIRST (before the spec
    gate), so the parity check uses ``assigned_to=agent_id`` to keep
    that pre-spec ownership check passing.
    """
    agent_id = uuid4()
    task_id = uuid4()
    task = MagicMock(
        id=task_id,
        status=status,
        task_type="code",
        # Owned by caller so _verify_qa_owner does not surface a non-spec
        # not_authorized before the spec gate runs.
        assigned_to=agent_id,
        qa_evidence_inspected=True,
        commits=[],
        pr_number=None,
        branch_name="feature/backend/abc",
        parent_task_id=None,
        sequence=0,
        team="backend",
        title="t",
        quick_context=None,
        pr_url="https://x/pr/8",
        work_session_id=None,
    )
    after = MagicMock(
        id=task_id,
        status="awaiting_documentation",
        assigned_to=agent_id,
        team="backend",
        pr_url="https://x/pr/8",
        qa_evidence_inspected=True,
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = task
    task_svc.agent_for.return_value = MagicMock(
        id=agent_id, role=role, team="backend", slug=None
    )
    task_svc.qa_pass.return_value = after
    task_svc.documenter_for_team.return_value = None
    task_svc.session = MagicMock()
    task_svc.session.begin_nested = MagicMock(
        return_value=MagicMock(
            __aenter__=AsyncMock(return_value=None),
            __aexit__=AsyncMock(return_value=False),
        )
    )
    deps = _make_deps(task_svc=task_svc)
    journal_svc = deps.journal
    journal_svc.has_learning_for_task.return_value = True
    c = Choreographer(deps)

    notes = (
        "Reviewed PR carefully. Branch convention correct. Commit prefix "
        "verified. README diff matches spec. All acceptance criteria met."
    )
    ctx = spec.Context(actor_id=agent_id, notes=notes)
    expected = spec.can_invoke_intent(spec.Role(role), "pass_review", task, ctx)

    env = await c.pass_review(agent_id, task_id, notes=notes)
    body = env.as_dict()
    if expected.allowed:
        # Spec allows. Verb-specific notes-length / journal:learning /
        # qa_evidence_inspected are wired to pass; the spec gate itself
        # must NOT be the source of any not_authorized rejection.
        assert body["error"] != "not_authorized", (
            f"role={role} status={status}: spec.can_invoke_intent allowed "
            f"but envelope surfaced not_authorized at the spec layer: {body}"
        )
    else:
        assert body["error"] == expected.rejection_kind, (
            f"role={role} status={status}: can_invoke_intent rejected with "
            f"{expected.rejection_kind}, got {body['error']!r}; full body: {body}"
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
async def test_fail_review_matches_spec(role: str, status: str) -> None:
    """Spec parity for fail_review's role + state gate.

    fail_review's IntentSpec composes ``("qa_fail",)`` and is QA-only.
    The spec gate enforces:

      - role == qa,
      - composed ``qa_fail`` action's source_status (AWAITING_QA),
      - self-review block (qa_fail.self_review_block=True).

    Same shape as pass_review: ownership precedes the spec gate, and
    the verb-specific notes-length / journal:learning /
    qa_evidence_inspected gates live in the verb body.
    """
    agent_id = uuid4()
    task_id = uuid4()
    task = MagicMock(
        id=task_id,
        status=status,
        task_type="code",
        # Owned by caller so _verify_qa_owner does not surface a non-spec
        # not_authorized before the spec gate runs.
        assigned_to=agent_id,
        qa_evidence_inspected=True,
        commits=[],
        pr_number=None,
        branch_name="feature/backend/abc",
        parent_task_id=None,
        sequence=0,
        team="backend",
        title="t",
        quick_context=None,
        work_session_id=None,
    )
    dev_id = uuid4()
    after = MagicMock(
        id=task_id,
        status="needs_revision",
        assigned_to=dev_id,
        team="backend",
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = task
    task_svc.agent_for.return_value = MagicMock(
        id=agent_id, role=role, team="backend", slug=None
    )
    task_svc.qa_fail.return_value = after
    task_svc.session = MagicMock()
    task_svc.session.begin_nested = MagicMock(
        return_value=MagicMock(
            __aenter__=AsyncMock(return_value=None),
            __aexit__=AsyncMock(return_value=False),
        )
    )
    deps = _make_deps(task_svc=task_svc)
    journal_svc = deps.journal
    journal_svc.has_learning_for_task.return_value = True
    c = Choreographer(deps)

    issues = [
        "Missing unit test coverage for /healthz endpoint — add at least one",
        "Lint errors in /api/foo.py: unused import and missing return type",
    ]
    notes = "Issues:\n" + "\n".join(f"- {i}" for i in issues)
    ctx = spec.Context(actor_id=agent_id, notes=notes, issues=tuple(issues))
    expected = spec.can_invoke_intent(spec.Role(role), "fail_review", task, ctx)

    env = await c.fail_review(agent_id, task_id, issues=issues)
    body = env.as_dict()
    if expected.allowed:
        # Spec allows. Verb-specific gates wired to pass; the spec gate
        # itself must NOT be the source of any not_authorized rejection.
        assert body["error"] != "not_authorized", (
            f"role={role} status={status}: spec.can_invoke_intent allowed "
            f"but envelope surfaced not_authorized at the spec layer: {body}"
        )
    else:
        assert body["error"] == expected.rejection_kind, (
            f"role={role} status={status}: can_invoke_intent rejected with "
            f"{expected.rejection_kind}, got {body['error']!r}; full body: {body}"
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
async def test_claim_doc_task_matches_spec(role: str, status: str) -> None:
    """Spec parity for claim_doc_task's role + claim source-status gate.

    claim_doc_task's IntentSpec composes ``("claim", "start")`` and is
    restricted to documenter. The spec gate enforces:

      - role == documenter,
      - claim's source_statuses,
      - CLAIM_RULES narrowing (documenter only from PENDING /
        AWAITING_DOCUMENTATION).

    The verb body owns dispatch via ``task.doc_claim`` (not the runner's
    claim+start chain) because the runtime semantic is "documenter
    inspects, status stays at awaiting_documentation" — see doc.py
    module docstring.
    """
    agent_id = uuid4()
    task_id = uuid4()
    task = MagicMock(
        id=task_id,
        status=status,
        task_type="code",
        assigned_to=None,
        commits=[],
        pr_number=None,
        branch_name="feature/backend/abc",
        parent_task_id=None,
        sequence=0,
        team="backend",
        title="t",
        quick_context=None,
        work_session_id=None,
    )
    after = MagicMock(
        id=task_id,
        status=status,
        assigned_to=agent_id,
        team="backend",
        branch_name="feature/backend/abc",
        work_session_id=None,
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = task
    task_svc.agent_for.return_value = MagicMock(
        id=agent_id, role=role, team="backend", slug=None
    )
    task_svc.doc_claim.return_value = after
    task_svc.list_in_progress_for_agent.return_value = []
    task_svc.list_paused_for_agent.return_value = []
    deps = _make_deps(task_svc=task_svc)
    c = Choreographer(deps)

    ctx = spec.Context(actor_id=agent_id)
    expected = spec.can_invoke_intent(spec.Role(role), "claim_doc_task", task, ctx)

    env = await c.claim_doc_task(agent_id, task_id)
    body = env.as_dict()
    if expected.allowed:
        # Spec allows. Verb may still surface a non-error envelope or a
        # downstream behavioral guard rejection; the spec gate itself
        # must NOT be the source of any not_authorized rejection.
        assert body["error"] != "not_authorized", (
            f"role={role} status={status}: spec.can_invoke_intent allowed "
            f"but envelope surfaced not_authorized at the spec layer: {body}"
        )
    else:
        assert body["error"] == expected.rejection_kind, (
            f"role={role} status={status}: can_invoke_intent rejected with "
            f"{expected.rejection_kind}, got {body['error']!r}; full body: {body}"
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
async def test_i_documented_matches_spec(role: str, status: str) -> None:
    """Spec parity for i_documented's role + state gate.

    i_documented's IntentSpec composes ``("docs_complete",)`` and is
    documenter-only. The spec gate enforces:

      - role == documenter,
      - composed ``docs_complete`` action's source_status
        (AWAITING_DOCUMENTATION),
      - self-review block (docs_complete.self_review_block=True; the
        verb body builds a Context with actor_slug +
        original_developer_slug so the spec naturally rejects
        self-review).

    The verb-specific gates (notes-length / files-list) live in the
    verb body — not modelled by the spec. They're wired to pass here so
    the spec gate is the load-bearing rejector. ``_verify_doc_owner``
    runs FIRST (before the spec gate), so the parity check uses
    ``assigned_to=agent_id`` to keep that pre-spec ownership check
    passing.
    """
    agent_id = uuid4()
    task_id = uuid4()
    task = MagicMock(
        id=task_id,
        status=status,
        task_type="code",
        # Owned by caller so _verify_doc_owner does not surface a non-spec
        # not_authorized before the spec gate runs.
        assigned_to=agent_id,
        commits=[],
        pr_number=None,
        branch_name="feature/backend/abc",
        parent_task_id=None,
        sequence=0,
        team="backend",
        title="t",
        quick_context=None,
        documents=[],
    )
    after = MagicMock(
        id=task_id,
        status="awaiting_pm_review",
        assigned_to=agent_id,
        team="backend",
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = task
    task_svc.agent_for.return_value = MagicMock(
        id=agent_id, role=role, team="backend", slug=None
    )
    task_svc.docs_complete.return_value = after
    task_svc.cell_pm_for_team.return_value = None
    task_svc.session = MagicMock()
    task_svc.session.flush = AsyncMock()
    task_svc.session.begin_nested = MagicMock(
        return_value=MagicMock(
            __aenter__=AsyncMock(return_value=None),
            __aexit__=AsyncMock(return_value=False),
        )
    )
    deps = _make_deps(task_svc=task_svc)
    c = Choreographer(deps)

    notes = "Wrote backend/guides/feature-x.md with usage examples + config notes."
    files = ["backend/guides/feature-x.md"]
    ctx = spec.Context(actor_id=agent_id, notes=notes, files=tuple(files))
    expected = spec.can_invoke_intent(spec.Role(role), "i_documented", task, ctx)

    env = await c.i_documented(agent_id, task_id, notes=notes, files=files)
    body = env.as_dict()
    if expected.allowed:
        # Spec allows. Verb-specific notes-length / files-list are wired
        # to pass; the spec gate itself must NOT be the source of any
        # not_authorized rejection.
        assert body["error"] != "not_authorized", (
            f"role={role} status={status}: spec.can_invoke_intent allowed "
            f"but envelope surfaced not_authorized at the spec layer: {body}"
        )
    else:
        assert body["error"] == expected.rejection_kind, (
            f"role={role} status={status}: can_invoke_intent rejected with "
            f"{expected.rejection_kind}, got {body['error']!r}; full body: {body}"
        )
