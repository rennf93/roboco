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
- protected base: master/main refused only when MIS-resolved — a branch-bearing
  parent exists (base should have been that branch) or the parent row is
  missing/corrupt. A standalone (parentless) task and a child of a branchless
  coordination parent legitimately rebase onto master (it IS their merge
  target; the push only ever hits the task branch). ``-``-refs always refused.
- git failure: sync_task_branch raises → invalid_state, steer to i_am_blocked
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from roboco.services.base import ValidationError
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
        t, base_branch=_BASE, actor_agent_id=aid, stash=False
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
async def test_sync_branch_refuses_master_base_with_branch_bearing_parent() -> None:
    aid = uuid4()
    tid = uuid4()
    t = _task(tid=tid, aid=aid)
    t.parent_task_id = uuid4()
    task_svc = AsyncMock()
    # Both the task fetch and the parent fetch resolve to a branch-bearing row:
    # the base should have been the parent's branch, so master is mis-resolved.
    task_svc.get.return_value = t
    task_svc.agent_for.return_value = MagicMock(role="developer", team="backend")
    git_svc = AsyncMock()
    deps = _make_deps(task=task_svc, git=git_svc)
    c = Choreographer(deps)

    with patch(
        "roboco.services.gateway.choreographer._impl.resolve_parent_branch",
        new=AsyncMock(return_value="master"),
    ):
        env = await c.sync_branch(aid, tid)

    assert env.error == "invalid_state"
    assert "protected" in (env.message or "")
    git_svc.sync_task_branch.assert_not_awaited()


@pytest.mark.asyncio
async def test_sync_branch_allows_master_base_for_standalone_task() -> None:
    aid = uuid4()
    tid = uuid4()
    t = _task(tid=tid, aid=aid)
    # Parentless standalone task (video / ci-watch / dep-update): master IS
    # the merge target, so the rebase must go through.
    t.parent_task_id = None
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.agent_for.return_value = MagicMock(role="developer", team="backend")
    git_svc = AsyncMock()
    git_svc.sync_task_branch.return_value = {"status": "rebased", "commits_rebased": 2}
    deps = _make_deps(task=task_svc, git=git_svc)
    c = Choreographer(deps)

    with patch(
        "roboco.services.gateway.choreographer._impl.resolve_parent_branch",
        new=AsyncMock(return_value="master"),
    ):
        env = await c.sync_branch(aid, tid)

    assert env.error is None
    git_svc.sync_task_branch.assert_awaited_once_with(
        t, base_branch="master", actor_agent_id=aid, stash=False
    )


@pytest.mark.asyncio
async def test_sync_branch_allows_master_base_for_branchless_parent_child() -> None:
    aid = uuid4()
    tid = uuid4()
    t = _task(tid=tid, aid=aid)
    t.parent_task_id = uuid4()
    branchless_parent = MagicMock(branch_name=None)
    task_svc = AsyncMock()
    # First get: the task itself; second get: the branchless coordination
    # parent — its child was cut from master, so master is the true base.
    task_svc.get.side_effect = [t, branchless_parent]
    task_svc.agent_for.return_value = MagicMock(role="developer", team="backend")
    git_svc = AsyncMock()
    git_svc.sync_task_branch.return_value = {"status": "rebased", "commits_rebased": 1}
    deps = _make_deps(task=task_svc, git=git_svc)
    c = Choreographer(deps)

    with patch(
        "roboco.services.gateway.choreographer._impl.resolve_parent_branch",
        new=AsyncMock(return_value="master"),
    ):
        env = await c.sync_branch(aid, tid)

    assert env.error is None
    git_svc.sync_task_branch.assert_awaited_once_with(
        t, base_branch="master", actor_agent_id=aid, stash=False
    )


@pytest.mark.asyncio
async def test_sync_branch_refuses_master_base_when_parent_row_missing() -> None:
    aid = uuid4()
    tid = uuid4()
    t = _task(tid=tid, aid=aid)
    t.parent_task_id = uuid4()
    task_svc = AsyncMock()
    task_svc.get.side_effect = [t, None]
    task_svc.agent_for.return_value = MagicMock(role="developer", team="backend")
    git_svc = AsyncMock()
    deps = _make_deps(task=task_svc, git=git_svc)
    c = Choreographer(deps)

    with patch(
        "roboco.services.gateway.choreographer._impl.resolve_parent_branch",
        new=AsyncMock(return_value="master"),
    ):
        env = await c.sync_branch(aid, tid)

    assert env.error == "invalid_state"
    git_svc.sync_task_branch.assert_not_awaited()


@pytest.mark.asyncio
async def test_sync_branch_refuses_injection_ref_unconditionally() -> None:
    aid = uuid4()
    tid = uuid4()
    t = _task(tid=tid, aid=aid)
    t.parent_task_id = None
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.agent_for.return_value = MagicMock(role="developer", team="backend")
    git_svc = AsyncMock()
    deps = _make_deps(task=task_svc, git=git_svc)
    c = Choreographer(deps)

    with patch(
        "roboco.services.gateway.choreographer._impl.resolve_parent_branch",
        new=AsyncMock(return_value="-evil-ref"),
    ):
        env = await c.sync_branch(aid, tid)

    assert env.error == "invalid_state"
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
async def test_sync_branch_protected_head_refusal_steers_to_i_am_blocked() -> None:
    """GitService.sync_task_branch's protected-HEAD-branch guard (2026-07-22
    follow-up — a mis-set branch_name matching master/main or a project's
    declared protected_branches) surfaces through the same generic
    invalid_state/i_am_blocked path as any other git failure — the
    choreographer doesn't need to special-case it, only propagate it."""
    aid = uuid4()
    tid = uuid4()
    t = _task(tid=tid, aid=aid)
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.agent_for.return_value = MagicMock(role="developer", team="backend")
    git_svc = AsyncMock()
    git_svc.sync_task_branch.side_effect = ValidationError(
        f"REBASE_FORBIDDEN: task branch_name '{_BRANCH}' is a protected branch"
    )
    deps = _make_deps(task=task_svc, git=git_svc)
    c = Choreographer(deps)

    with patch(
        "roboco.services.gateway.choreographer._impl.resolve_parent_branch",
        new=AsyncMock(return_value=_BASE),
    ):
        env = await c.sync_branch(aid, tid)

    assert env.error == "invalid_state"
    assert "REBASE_FORBIDDEN" in (env.message or "")
    assert "i_am_blocked" in (env.remediate or "")


@pytest.mark.asyncio
async def test_sync_branch_passes_stash_flag_through() -> None:
    """stash=True on the verb forwards to GitService.sync_task_branch."""
    aid = uuid4()
    tid = uuid4()
    t = _task(tid=tid, aid=aid)
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.agent_for.return_value = MagicMock(role="developer", team="backend")
    git_svc = AsyncMock()
    git_svc.sync_task_branch.return_value = {"status": "rebased", "unique_commits": 1}
    deps = _make_deps(task=task_svc, git=git_svc)
    c = Choreographer(deps)

    with patch(
        "roboco.services.gateway.choreographer._impl.resolve_parent_branch",
        new=AsyncMock(return_value=_BASE),
    ):
        env = await c.sync_branch(aid, tid, stash=True)

    git_svc.sync_task_branch.assert_awaited_once_with(
        t, base_branch=_BASE, actor_agent_id=aid, stash=True
    )
    assert env.error is None


@pytest.mark.asyncio
async def test_sync_branch_dirty_workspace_failure_steers_to_stash_or_commit() -> None:
    """A DIRTY_WORKSPACE failure gets a specific, actionable remediate — not
    the generic i_am_blocked escalation."""
    aid = uuid4()
    tid = uuid4()
    t = _task(tid=tid, aid=aid)
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.agent_for.return_value = MagicMock(role="developer", team="backend")
    git_svc = AsyncMock()
    git_svc.sync_task_branch.side_effect = RuntimeError(
        "DIRTY_WORKSPACE: Cannot rebase with uncommitted changes."
    )
    deps = _make_deps(task=task_svc, git=git_svc)
    c = Choreographer(deps)

    with patch(
        "roboco.services.gateway.choreographer._impl.resolve_parent_branch",
        new=AsyncMock(return_value=_BASE),
    ):
        env = await c.sync_branch(aid, tid)

    assert env.error == "invalid_state"
    assert "stash=True" in (env.remediate or "")
    assert "commit(" in (env.remediate or "")


@pytest.mark.asyncio
async def test_sync_branch_conflicts_with_stash_preserved_notes_it_in_next() -> None:
    """A conflict with stash_preserved=True tells the dev their stash is safe."""
    aid = uuid4()
    tid = uuid4()
    t = _task(tid=tid, aid=aid)
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.agent_for.return_value = MagicMock(role="developer", team="backend")
    git_svc = AsyncMock()
    git_svc.sync_task_branch.return_value = {
        "status": "conflicts",
        "files": ["src/a.py"],
        "stash_preserved": True,
    }
    deps = _make_deps(task=task_svc, git=git_svc)
    c = Choreographer(deps)

    with patch(
        "roboco.services.gateway.choreographer._impl.resolve_parent_branch",
        new=AsyncMock(return_value=_BASE),
    ):
        env = await c.sync_branch(aid, tid, stash=True)

    assert env.error is None
    assert "stash" in (env.next or "").lower()


@pytest.mark.asyncio
async def test_sync_branch_stash_pop_conflict_notes_preserved_stash() -> None:
    """A clean rebase whose stash pop conflicted must not read as a plain
    success — the dev still has manual work to finish."""
    aid = uuid4()
    tid = uuid4()
    t = _task(tid=tid, aid=aid)
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.agent_for.return_value = MagicMock(role="developer", team="backend")
    git_svc = AsyncMock()
    git_svc.sync_task_branch.return_value = {
        "status": "rebased",
        "unique_commits": 2,
        "stash_pop_conflict": True,
    }
    deps = _make_deps(task=task_svc, git=git_svc)
    c = Choreographer(deps)

    with patch(
        "roboco.services.gateway.choreographer._impl.resolve_parent_branch",
        new=AsyncMock(return_value=_BASE),
    ):
        env = await c.sync_branch(aid, tid, stash=True)

    assert env.error is None
    assert "conflict" in (env.next or "").lower()
    assert "preserved" in (env.next or "").lower()


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
