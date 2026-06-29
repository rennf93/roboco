"""open_pr's idempotent re-entry guard reads ``t.pr_number`` from an unlocked
fetch, so two CONCURRENT (respawn-race) retries both pass the guard and both
emit the 70% "opened PR #N" milestone progress entry — double-counting one
PR-open event in the Progress tab + cycle-time metrics (no PR duplication;
GitHub's 422 'already exists' guard holds).

The fix: a PostgreSQL transaction-scoped advisory lock keyed by the task id
(seed ``2``, disjoint from the per-agent claim lock seed ``0`` and the
per-parent delegate lock seed ``1``) acquired at the top of ``open_pr``
BEFORE the ``t = await self.task.get(...)`` fetch and held through
``_record_milestone_progress`` + the outer request commit. The second
same-task concurrent ``open_pr`` blocks until the first commits; its fetch
then sees the committed ``pr_number``, the idempotent guard fires, and it
short-circuits without re-emitting. Per-TASK (not per-agent): the
single-active-task guard means a dev has one task at a time, so concurrent
``open_pr`` on the SAME task is purely the respawn-race case — no legitimate
concurrency is regressed.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.services.gateway.choreographer import Choreographer, ChoreographerDeps


def _make_deps(task: AsyncMock, git: AsyncMock | None = None) -> ChoreographerDeps:
    base: dict[str, Any] = {
        "task": task,
        "work_session": AsyncMock(),
        "git": git or AsyncMock(),
        "a2a": AsyncMock(),
        "journal": AsyncMock(),
        "audit": AsyncMock(),
        "evidence_repo": AsyncMock(),
    }
    repo = base["evidence_repo"]
    for m in (
        "list_unread_a2a",
        "list_unread_mentions",
        "list_pending_notifications",
        "task_metadata_gaps",
        "recent_team_activity",
        "blockers_in_lane",
    ):
        getattr(repo, m).return_value = []
    return ChoreographerDeps(**base)


def _wire_savepoint(task_svc: AsyncMock) -> None:
    """Stub task_svc.session.begin_nested() as an async context manager."""
    task_svc.session = MagicMock()
    task_svc.session.begin_nested = MagicMock(
        return_value=MagicMock(
            __aenter__=AsyncMock(return_value=None),
            __aexit__=AsyncMock(return_value=False),
        )
    )


def _task_with_pr(*, pr_number: int | None) -> MagicMock:
    return MagicMock(
        id=uuid4(),
        status="in_progress",
        assigned_to=None,  # set per-test
        plan="x",
        commits=[{"sha": "abc"}],
        pr_number=pr_number,
        parent_task_id=None,
        branch_name="feature/backend/abc12345",
    )


@pytest.mark.asyncio
async def test_open_pr_acquires_task_lock_before_fetch() -> None:
    """The per-task advisory lock MUST be acquired before the first
    ``task.get`` fetch (the read the idempotent re-entry guard consults for
    ``t.pr_number``). This is the ordering that closes the TOCTOU: the second
    concurrent same-task open_pr blocks on the lock before its fetch, so once
    the first commits its ``pr_number`` the second's fetch sees it and the
    idempotent guard short-circuits without re-emitting the milestone. A lock
    acquired AFTER the fetch would leave the second call's ``t`` stale
    (fetched before the first committed, ``pr_number=None``) — the guard would
    not fire and the milestone would re-emit, so 'lock before runner' alone is
    insufficient; the lock must precede the fetch."""
    aid = uuid4()
    tid = uuid4()
    t = _task_with_pr(pr_number=None)
    t.assigned_to = aid
    t.id = tid
    t_after = _task_with_pr(pr_number=42)
    t_after.assigned_to = aid
    t_after.id = tid
    t_after.pr_url = "https://gh/x/42"
    task_svc = AsyncMock()
    task_svc.agent_for.return_value = MagicMock(role="developer", team="backend")
    _wire_savepoint(task_svc)
    git_svc = AsyncMock()
    git_svc.push_branch.return_value = ("feature/backend/abc12345", 1)
    git_svc.create_pr.return_value = {
        "pr_number": 42,
        "pr_url": "https://gh/x/42",
        "is_root_pr": False,
    }

    # Shared call-order recorder: the lock must precede the first get fetch.
    # First fetch (idempotent guard read) returns pr_number=None; the
    # post-runner re-fetch in _open_pr_success_envelope returns pr_number=42.
    calls: list[str] = []
    staged: list[Any] = [t, t_after]

    async def _lock(_tid: object) -> None:
        calls.append("lock")

    async def _get_staged(_tid: object) -> Any:
        calls.append("get")
        return staged.pop(0) if staged else t_after

    task_svc.acquire_task_lock = _lock
    task_svc.get.side_effect = _get_staged

    deps = _make_deps(task_svc, git_svc)
    c = Choreographer(deps)

    env = await c.open_pr(aid, tid)
    assert env.error is None, env.as_dict()

    # The flow reached the milestone (otherwise the lock-ordering assertion
    # would pass for the wrong reason — a short-circuit before the runner).
    assert calls.count("get") >= 1, calls

    # The lock was acquired exactly once, before the first fetch.
    assert calls.count("lock") == 1, calls
    assert calls.index("lock") < calls.index("get"), (
        f"task lock must be acquired before the first task.get fetch; order was {calls}"
    )


@pytest.mark.asyncio
async def test_open_pr_emits_milestone_once_with_task_lock() -> None:
    """No-regression: acquiring the per-task lock must not break the normal
    open_pr path — the PR is still opened (git.create_pr awaited), the
    milestone is still emitted exactly ONCE (add_progress awaited once with
    the "opened PR #N" message), and the lock is awaited once with the task
    id. The lock is transparent to the happy path."""
    aid = uuid4()
    tid = uuid4()
    t = _task_with_pr(pr_number=None)
    t.assigned_to = aid
    t.id = tid
    t_after = _task_with_pr(pr_number=42)
    t_after.assigned_to = aid
    t_after.id = tid
    t_after.pr_url = "https://gh/x/42"
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
    # Leave the default AsyncMock for acquire_task_lock (transparent no-op).
    deps = _make_deps(task_svc, git_svc)
    c = Choreographer(deps)

    env = await c.open_pr(aid, tid)
    assert env.error is None, env.as_dict()
    git_svc.create_pr.assert_awaited_once()
    # The milestone emitted exactly once — the 70% "opened PR #42" entry.
    task_svc.add_progress.assert_awaited_once()
    assert "opened PR #42" in str(task_svc.add_progress.call_args)
    task_svc.acquire_task_lock.assert_awaited_once_with(tid)
