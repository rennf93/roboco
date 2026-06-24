"""open_pr pushes the current branch and opens a PR.

Gate E (commit c5c2016) made `i_am_done` strict — requires `pr_number` set.
The catch-up flow is off the dev manifest. Devs need an explicit verb that
pushes + opens the PR so a subsequent `i_am_done` can do the strict submit.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.services.gateway.choreographer import Choreographer, ChoreographerDeps


def _make_deps(**overrides: AsyncMock) -> ChoreographerDeps:
    """Local dep-builder. Established pattern: per-test-file, not centralized."""
    task = overrides.get("task", AsyncMock())
    work_session = overrides.get("work_session", AsyncMock())
    git = overrides.get("git", AsyncMock())
    a2a = overrides.get("a2a", AsyncMock())
    journal = overrides.get("journal", AsyncMock())
    audit = overrides.get("audit", AsyncMock())
    evidence_repo = overrides.get("evidence_repo", AsyncMock())
    for method in (
        "list_unread_a2a",
        "list_unread_mentions",
        "list_pending_notifications",
        "task_metadata_gaps",
        "recent_team_activity",
        "blockers_in_lane",
    ):
        getattr(evidence_repo, method).return_value = []
    return ChoreographerDeps(
        task=task,
        work_session=work_session,
        git=git,
        a2a=a2a,
        journal=journal,
        audit=audit,
        evidence_repo=evidence_repo,
    )


def _wire_savepoint(task_svc: AsyncMock) -> None:
    """Stub task_svc.session.begin_nested() as an async context manager.

    The VerbRunner wraps composed atomic actions in `session.begin_nested()`.
    open_pr has composes=() so the savepoint body is empty, but the
    context manager is still entered/exited. Tests need a no-op stub.
    """
    task_svc.session = MagicMock()
    task_svc.session.begin_nested = MagicMock(
        return_value=MagicMock(
            __aenter__=AsyncMock(return_value=None),
            __aexit__=AsyncMock(return_value=False),
        )
    )


@pytest.mark.asyncio
async def test_open_pr_pushes_and_opens_pr() -> None:
    aid = uuid4()
    tid = uuid4()
    t = MagicMock(
        id=tid,
        status="in_progress",
        assigned_to=aid,
        plan="x",
        commits=[{"sha": "abc"}],
        pr_number=None,
        # No parent → _do_create_pr falls back to parent_branch_for; this
        # test asserts push+create mechanics, not the cell→root base (#181,
        # covered in test_verb_runner).
        parent_task_id=None,
        branch_name="feature/backend/abc12345",
    )
    # Re-fetched task post-runner has the new pr_number written by
    # git_service.create_pr's _record_pr_atomically.
    t_after = MagicMock(
        id=tid,
        status="in_progress",
        assigned_to=aid,
        plan="x",
        commits=[{"sha": "abc"}],
        pr_number=42,
        pr_url="https://gh/x/42",
        parent_task_id=None,
        branch_name="feature/backend/abc12345",
    )
    task_svc = AsyncMock()
    task_svc.get.side_effect = [t, t_after]
    task_svc.agent_for.return_value = MagicMock(role="developer", team="backend")
    _wire_savepoint(task_svc)
    git_svc = AsyncMock()
    git_svc.push_branch.return_value = ("feature/backend/abc12345", 1)
    git_svc.create_pr.return_value = {
        "pr_number": 42,
        "pr_url": "https://gh/x/42",
        "is_root_pr": False,
    }
    work_session_svc = AsyncMock()
    deps = _make_deps(task=task_svc, git=git_svc, work_session=work_session_svc)
    c = Choreographer(deps)

    env = await c.open_pr(aid, tid)

    git_svc.push_branch.assert_awaited()
    git_svc.create_pr.assert_awaited()
    assert env.error is None
    assert env.next is not None
    # spec's next_hint("open_pr") returns the canonical i_am_done remediation;
    # PR number is surfaced via introspection.with_introspection() rather
    # than the next-hint string itself.
    assert "i_am_done" in env.next


@pytest.mark.asyncio
async def test_open_pr_reassigned_steers_to_give_me_work() -> None:
    """A stale agent (task reassigned away) gets a clear not_authorized that
    steers to give_me_work — NOT the owns_task tracing_gap it would retry.

    The reassignment short-circuit runs before the spec gate, so the agent
    never sees the misleading 'fixable precondition' framing that drove the
    observed open_pr owns_task retry-loops.
    """
    aid = uuid4()
    other = uuid4()
    tid = uuid4()
    t = MagicMock(
        id=tid,
        status="in_progress",
        assigned_to=other,
        plan="x",
        commits=[{"sha": "abc"}],
        pr_number=None,
        branch_name="feature/backend/abc12345",
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.agent_for.return_value = MagicMock(role="developer", team="backend")
    git_svc = AsyncMock()
    deps = _make_deps(task=task_svc, git=git_svc)
    c = Choreographer(deps)

    env = await c.open_pr(aid, tid)

    git_svc.push_branch.assert_not_awaited()
    git_svc.create_pr.assert_not_awaited()
    assert env.error == "not_authorized"
    assert "no longer assigned" in (env.message or "").lower()
    assert "give_me_work" in (env.remediate or "")


@pytest.mark.asyncio
async def test_open_pr_empty_diff_steers_to_blocked_not_retry() -> None:
    """An empty-diff subtask (branch has no commits vs base → GitHub 422
    'No commits between') gets a terminal i_am_blocked steer, not a generic
    'retry' invalid_state that loops the dev forever."""
    aid = uuid4()
    tid = uuid4()
    t = MagicMock(
        id=tid,
        status="in_progress",
        assigned_to=aid,
        plan="x",
        commits=[{"sha": "abc"}],
        pr_number=None,
        parent_task_id=None,
        branch_name="feature/backend/abc12345",
    )
    task_svc = AsyncMock()
    task_svc.get.side_effect = [t, t]
    task_svc.agent_for.return_value = MagicMock(role="developer", team="backend")
    _wire_savepoint(task_svc)
    git_svc = AsyncMock()
    git_svc.push_branch.return_value = ("feature/backend/abc12345", 1)
    git_svc.create_pr.side_effect = Exception(
        'GitHub API refused PR creation (422): {"message":"Validation Failed",'
        '"errors":[{"message":"No commits between feature/backend/abc12345 and '
        'feature/backend/abc12345--child"}]}'
    )
    deps = _make_deps(task=task_svc, git=git_svc)
    c = Choreographer(deps)

    env = await c.open_pr(aid, tid)

    assert env.error == "invalid_state"
    assert "no commits" in (env.message or "").lower()
    assert "i_am_blocked" in (env.remediate or "")
    assert "do not retry" in (env.remediate or "").lower()


@pytest.mark.asyncio
async def test_open_pr_rejects_when_no_commits() -> None:
    aid = uuid4()
    tid = uuid4()
    t = MagicMock(
        id=tid,
        status="in_progress",
        assigned_to=aid,
        plan="x",
        commits=[],
        pr_number=None,
        branch_name="feature/backend/abc12345",
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.agent_for.return_value = MagicMock(role="developer", team="backend")
    git_svc = AsyncMock()
    deps = _make_deps(task=task_svc, git=git_svc)
    c = Choreographer(deps)

    env = await c.open_pr(aid, tid)

    git_svc.push_branch.assert_not_awaited()
    git_svc.create_pr.assert_not_awaited()
    # Spec's PRECONDITION_COMMITS surfaces as tracing_gap; remediate
    # still mentions committing.
    assert env.error == "tracing_gap"
    assert env.missing == ["commits>=1"]
    assert env.remediate is not None
    assert "commit" in env.remediate.lower()


@pytest.mark.asyncio
async def test_open_pr_idempotent_when_pr_already_open() -> None:
    aid = uuid4()
    tid = uuid4()
    t = MagicMock(
        id=tid,
        status="in_progress",
        assigned_to=aid,
        plan="x",
        commits=[{"sha": "abc"}],
        pr_number=7,
        branch_name="feature/backend/abc12345",
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    git_svc = AsyncMock()
    deps = _make_deps(task=task_svc, git=git_svc)
    c = Choreographer(deps)

    env = await c.open_pr(aid, tid)

    git_svc.push_branch.assert_not_awaited()
    git_svc.create_pr.assert_not_awaited()
    assert env.error is None
    assert env.next is not None
    assert "7" in env.next


@pytest.mark.asyncio
async def test_open_pr_returns_not_found_for_unknown_task() -> None:
    aid = uuid4()
    tid = uuid4()
    task_svc = AsyncMock()
    task_svc.get.return_value = None
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.open_pr(aid, tid)

    assert env.error == "not_found"
