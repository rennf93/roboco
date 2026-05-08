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
        branch_name="feature/backend/abc12345",
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = t
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
    assert "42" in env.next  # remediate points to i_am_done with PR ref


@pytest.mark.asyncio
async def test_open_pr_rejects_when_not_assigned() -> None:
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
    git_svc = AsyncMock()
    deps = _make_deps(task=task_svc, git=git_svc)
    c = Choreographer(deps)

    env = await c.open_pr(aid, tid)

    git_svc.push_branch.assert_not_awaited()
    git_svc.create_pr.assert_not_awaited()
    assert env.error == "not_authorized"


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
    git_svc = AsyncMock()
    deps = _make_deps(task=task_svc, git=git_svc)
    c = Choreographer(deps)

    env = await c.open_pr(aid, tid)

    git_svc.push_branch.assert_not_awaited()
    git_svc.create_pr.assert_not_awaited()
    assert env.error == "invalid_state"
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
