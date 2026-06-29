"""sync_branch rebases the caller's task branch onto its base THROUGH the gate.

Multi-level sequencing Phase B1. Raw shell git is denied to agents
(``Bash(git:*)`` base deny), so a developer whose branch fell behind its base
had no gate-level rebase — only the CEO/PM-only ``/rebase`` HTTP route.
``sync_branch`` is the dev verb that wraps the rebase through the gate
(traced + evidenced), so the "everything goes through the gates" invariant
holds. These tests pin the handler:

- happy path: git.sync_task_branch runs, evidence carries the rebase result,
  heartbeat fires
- conflicts: rebase aborted, next_hint points the dev at resolve-by-hand
- not_found: unknown task id
- not_authorized: only the current claimant can sync (ownership gate)
- no branch: branchless / not-yet-claimed task → invalid_state, steer to
  i_will_work_on
- protected base: resolved base == master/main → invalid_state (defense-in-depth)
- git failure: sync_task_branch raises → invalid_state, steer to i_am_blocked
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from roboco.services.gateway.choreographer import Choreographer, ChoreographerDeps


def _make_deps(**overrides: object) -> ChoreographerDeps:
    base: dict[str, object] = {
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
    assert isinstance(repo, AsyncMock)
    for method in (
        "list_unread_a2a",
        "list_unread_mentions",
        "list_pending_notifications",
        "task_metadata_gaps",
        "recent_team_activity",
        "blockers_in_lane",
    ):
        getattr(repo, method).return_value = []
    return ChoreographerDeps(**base)


_BRANCH = "feature/backend/abc12345"
_BASE = "feature/backend/parent12345"


def _task(*, tid: object, aid: object, branch: str | None = _BRANCH) -> MagicMock:
    return MagicMock(
        id=tid,
        status="in_progress",
        assigned_to=aid,
        branch_name=branch,
    )


@pytest.mark.asyncio
async def test_sync_branch_rebases_and_returns_evidence() -> None:
    aid = uuid4()
    tid = uuid4()
    t = _task(tid=tid, aid=aid)
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.agent_for.return_value = MagicMock(role="developer", team="backend")
    git_svc = AsyncMock()
    git_svc.sync_task_branch.return_value = {
        "status": "rebased",
        "commits_rebased": 3,
    }
    deps = _make_deps(task=task_svc, git=git_svc)
    c = Choreographer(deps)

    with patch(
        "roboco.services.gateway.choreographer._impl.resolve_parent_branch",
        new=AsyncMock(return_value=_BASE),
    ):
        env = await c.sync_branch(aid, tid)

    git_svc.sync_task_branch.assert_awaited_once_with(
        t, base_branch=_BASE, actor_agent_id=aid
    )
    assert env.error is None
    assert env.evidence is not None
    assert env.evidence["base_branch"] == _BASE
    assert env.evidence["head_branch"] == _BRANCH
    assert env.evidence["rebase"]["status"] == "rebased"
    task_svc.heartbeat.assert_awaited_once_with(tid)


@pytest.mark.asyncio
async def test_sync_branch_conflicts_aborts_and_steers_to_resolve() -> None:
    aid = uuid4()
    tid = uuid4()
    t = _task(tid=tid, aid=aid)
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.agent_for.return_value = MagicMock(role="developer", team="backend")
    git_svc = AsyncMock()
    git_svc.sync_task_branch.return_value = {
        "status": "conflicts",
        "files": ["src/a.py", "src/b.py"],
    }
    deps = _make_deps(task=task_svc, git=git_svc)
    c = Choreographer(deps)

    with patch(
        "roboco.services.gateway.choreographer._impl.resolve_parent_branch",
        new=AsyncMock(return_value=_BASE),
    ):
        env = await c.sync_branch(aid, tid)

    assert env.error is None
    assert env.next is not None
    assert "resolve by hand" in env.next
    assert "sync_branch again" in env.next


@pytest.mark.asyncio
async def test_sync_branch_not_found_for_unknown_task() -> None:
    aid = uuid4()
    tid = uuid4()
    task_svc = AsyncMock()
    task_svc.get.return_value = None
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.sync_branch(aid, tid)

    assert env.error == "not_found"


@pytest.mark.asyncio
async def test_sync_branch_rejects_when_not_claimant() -> None:
    aid = uuid4()
    other = uuid4()
    tid = uuid4()
    t = _task(tid=tid, aid=other)
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.agent_for.return_value = MagicMock(role="developer", team="backend")
    git_svc = AsyncMock()
    deps = _make_deps(task=task_svc, git=git_svc)
    c = Choreographer(deps)

    env = await c.sync_branch(aid, tid)

    # PRECONDITION_OWNERSHIP rejects a non-owner as not_authorized.
    assert env.error == "not_authorized"
    git_svc.sync_task_branch.assert_not_awaited()


@pytest.mark.asyncio
async def test_sync_branch_no_branch_steers_to_i_will_work_on() -> None:
    aid = uuid4()
    tid = uuid4()
    # branch_name=None — task not yet claimed / branchless coordination root.
    t = _task(tid=tid, aid=aid, branch=None)
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.agent_for.return_value = MagicMock(role="developer", team="backend")
    git_svc = AsyncMock()
    deps = _make_deps(task=task_svc, git=git_svc)
    c = Choreographer(deps)

    env = await c.sync_branch(aid, tid)

    assert env.error == "invalid_state"
    assert "i_will_work_on" in (env.remediate or "")
    git_svc.sync_task_branch.assert_not_awaited()


@pytest.mark.asyncio
async def test_sync_branch_refuses_protected_base() -> None:
    aid = uuid4()
    tid = uuid4()
    t = _task(tid=tid, aid=aid)
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.agent_for.return_value = MagicMock(role="developer", team="backend")
    git_svc = AsyncMock()
    deps = _make_deps(task=task_svc, git=git_svc)
    c = Choreographer(deps)

    # Defense-in-depth: a base that resolved to master must never be rebased into.
    with patch(
        "roboco.services.gateway.choreographer._impl.resolve_parent_branch",
        new=AsyncMock(return_value="master"),
    ):
        env = await c.sync_branch(aid, tid)

    assert env.error == "invalid_state"
    assert "protected" in (env.message or "")
    git_svc.sync_task_branch.assert_not_awaited()


@pytest.mark.asyncio
async def test_sync_branch_git_failure_steers_to_i_am_blocked() -> None:
    aid = uuid4()
    tid = uuid4()
    t = _task(tid=tid, aid=aid)
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.agent_for.return_value = MagicMock(role="developer", team="backend")
    git_svc = AsyncMock()
    git_svc.sync_task_branch.side_effect = RuntimeError("network down")
    deps = _make_deps(task=task_svc, git=git_svc)
    c = Choreographer(deps)

    with patch(
        "roboco.services.gateway.choreographer._impl.resolve_parent_branch",
        new=AsyncMock(return_value=_BASE),
    ):
        env = await c.sync_branch(aid, tid)

    assert env.error == "invalid_state"
    assert "i_am_blocked" in (env.remediate or "")


@pytest.mark.asyncio
async def test_sync_branch_rejection_writes_audit_row() -> None:
    """Every rejection envelope must call audit.log_event (Task 6 contract)."""
    aid = uuid4()
    tid = uuid4()
    task_svc = AsyncMock()
    task_svc.get.return_value = None
    audit_svc = AsyncMock()
    deps = _make_deps(task=task_svc, audit=audit_svc)
    c = Choreographer(deps)

    env = await c.sync_branch(aid, tid)

    assert env.error == "not_found"
    audit_svc.log_event.assert_awaited_once()
    kwargs = audit_svc.log_event.await_args.kwargs
    assert kwargs["event_type"] == "gateway.rejected"
    assert kwargs["details"]["verb"] == "sync_branch"
