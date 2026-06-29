"""Tests for `pr_merge` concurrency hardening.

Two PMs completing different subtasks of the same parent could race on
the gh API merge call. The fix is to:

1. Take a row-level lock on the parent task before invoking the merge.
2. Retry once if GitHub returns 409 (merge conflict from racing merges),
   re-pulling the local target branch in between to refresh refs.

These are unit tests — concurrency is exercised via mocks (status code
sequence + assertions on `with_for_update` use), not real DB transactions.
The lock-acquisition path is asserted by checking that the parent-task
SELECT statement passed to `session.execute` carries `FOR UPDATE` semantics.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from roboco.exceptions import GitError
from roboco.services.git import GitService

# Module-level constants kept local so the assertions stay readable and
# ruff's PLR2004 magic-value rule has nothing to complain about. The
# threshold mirrors httpx's `Response.is_success` rule (status < 400).
# The expected-call counters document the retry contract: at most two
# merge attempts, two `_sync_target_branch` calls (refresh + post-success),
# two SELECTs (PR lookup + parent FOR UPDATE).
_HTTP_OK_THRESHOLD = 400
_EXPECTED_MERGE_ATTEMPTS = 2
_EXPECTED_SYNC_CALLS = 2
_EXPECTED_SELECT_CALLS = 2


def _make_session(
    pr_lookup_task: object,
    parent_task: object | None,
) -> MagicMock:
    """Build a session whose first execute returns the PR-owning task,
    and second execute (the SELECT FOR UPDATE on the parent) returns
    `parent_task`.
    """
    session = MagicMock()
    pr_result = MagicMock()
    pr_result.scalar_one_or_none.return_value = pr_lookup_task
    parent_result = MagicMock()
    parent_result.scalar_one_or_none.return_value = parent_task

    # First execute() = PR -> task lookup; second = parent lock; further
    # executes (none expected here) reuse parent_result.
    session.execute = AsyncMock(side_effect=[pr_result, parent_result])
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    session.flush = AsyncMock()
    return session


def _patch_project_service(project: object | None) -> Any:
    fake_service = MagicMock()
    fake_service.get = AsyncMock(return_value=project)
    fake_service.get_by_slug = AsyncMock(return_value=project)
    return patch("roboco.services.git.get_project_service", return_value=fake_service)


def _bind(svc: GitService, name: str, value: object) -> None:
    object.__setattr__(svc, name, value)


def _fake_response(status_code: int) -> MagicMock:
    resp = MagicMock()
    resp.is_success = status_code < _HTTP_OK_THRESHOLD
    resp.status_code = status_code
    resp.text = f"status {status_code}"
    return resp


# ---------------------------------------------------------------------------
# Scenario: GitHub returns 409 once, then 200. Retry must succeed and call
# `_sync_target_branch` between attempts to refresh local state.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pr_merge_retries_once_on_409_conflict() -> None:
    project_id = uuid4()
    parent_id = uuid4()
    fake_task = MagicMock(
        id=uuid4(),
        project_id=project_id,
        parent_task_id=parent_id,
        assigned_to=uuid4(),
        work_session_id=None,
    )
    fake_parent = MagicMock(id=parent_id)
    fake_project = MagicMock(slug="roboco")

    svc = GitService(_make_session(fake_task, fake_parent))
    _bind(svc, "get_workspace", AsyncMock(return_value=Path("/tmp/ws")))
    _bind(svc, "_get_project_token_or_raise", AsyncMock(return_value="tok"))
    _bind(svc, "_parse_github_remote", MagicMock(return_value=("acme", "repo")))

    call_seq = AsyncMock(side_effect=[_fake_response(409), _fake_response(200)])
    _bind(svc, "_call_merge_api", call_seq)
    _bind(svc, "_delete_pr_branch_best_effort", AsyncMock())
    sync_branch = AsyncMock(return_value="merged-sha")
    _bind(svc, "_sync_target_branch", sync_branch)

    with _patch_project_service(fake_project):
        out = await svc.pr_merge(
            11, target="feature/backend/parent", project_id=project_id
        )

    assert out == {"merge_commit_sha": "merged-sha"}
    # _call_merge_api invoked twice — once 409, once 200.
    assert call_seq.await_count == _EXPECTED_MERGE_ATTEMPTS
    # _sync_target_branch invoked twice — once for the 409 refresh, once
    # for the post-success refresh that returns the merge SHA.
    assert sync_branch.await_count == _EXPECTED_SYNC_CALLS


# ---------------------------------------------------------------------------
# Scenario: GitHub returns 409 twice. We don't loop indefinitely — bubble
# up GitError so the choreographer can return invalid_state.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pr_merge_raises_after_second_409() -> None:
    project_id = uuid4()
    parent_id = uuid4()
    fake_task = MagicMock(
        id=uuid4(),
        project_id=project_id,
        parent_task_id=parent_id,
        assigned_to=uuid4(),
        work_session_id=None,
    )
    fake_parent = MagicMock(id=parent_id)
    fake_project = MagicMock(slug="roboco")

    svc = GitService(_make_session(fake_task, fake_parent))
    _bind(svc, "get_workspace", AsyncMock(return_value=Path("/tmp/ws")))
    _bind(svc, "_get_project_token_or_raise", AsyncMock(return_value="tok"))
    _bind(svc, "_parse_github_remote", MagicMock(return_value=("acme", "repo")))

    call_seq = AsyncMock(side_effect=[_fake_response(409), _fake_response(409)])
    _bind(svc, "_call_merge_api", call_seq)
    _bind(svc, "_delete_pr_branch_best_effort", AsyncMock())
    _bind(svc, "_sync_target_branch", AsyncMock(return_value="abc"))

    with _patch_project_service(fake_project), pytest.raises(GitError) as exc_info:
        await svc.pr_merge(11, target="feature/backend/parent", project_id=project_id)

    assert "409" in str(exc_info.value)
    # No infinite retries — exactly two attempts.
    assert call_seq.await_count == _EXPECTED_MERGE_ATTEMPTS


# ---------------------------------------------------------------------------
# Scenario: Non-409 GitHub error is NOT retried; raises immediately.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pr_merge_does_not_retry_on_non_409_error() -> None:
    project_id = uuid4()
    parent_id = uuid4()
    fake_task = MagicMock(
        id=uuid4(),
        project_id=project_id,
        parent_task_id=parent_id,
        assigned_to=uuid4(),
        work_session_id=None,
    )
    fake_parent = MagicMock(id=parent_id)
    fake_project = MagicMock(slug="roboco")

    svc = GitService(_make_session(fake_task, fake_parent))
    _bind(svc, "get_workspace", AsyncMock(return_value=Path("/tmp/ws")))
    _bind(svc, "_get_project_token_or_raise", AsyncMock(return_value="tok"))
    _bind(svc, "_parse_github_remote", MagicMock(return_value=("acme", "repo")))

    call_seq = AsyncMock(side_effect=[_fake_response(422)])
    _bind(svc, "_call_merge_api", call_seq)
    _bind(svc, "_delete_pr_branch_best_effort", AsyncMock())
    _bind(svc, "_sync_target_branch", AsyncMock(return_value="abc"))

    with _patch_project_service(fake_project), pytest.raises(GitError):
        await svc.pr_merge(11, target="feature/backend/parent", project_id=project_id)

    # Only one merge attempt — non-409 error path skips retry.
    assert call_seq.await_count == 1


# ---------------------------------------------------------------------------
# Scenario: Locking the parent task — assert with_for_update is part of
# the SELECT statement issued for the parent task lookup.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pr_merge_locks_parent_task_with_for_update() -> None:
    project_id = uuid4()
    parent_id = uuid4()
    fake_task = MagicMock(
        id=uuid4(),
        project_id=project_id,
        parent_task_id=parent_id,
        assigned_to=uuid4(),
        work_session_id=None,
    )
    fake_parent = MagicMock(id=parent_id)
    fake_project = MagicMock(slug="roboco")

    session = _make_session(fake_task, fake_parent)
    svc = GitService(session)
    _bind(svc, "get_workspace", AsyncMock(return_value=Path("/tmp/ws")))
    _bind(svc, "_get_project_token_or_raise", AsyncMock(return_value="tok"))
    _bind(svc, "_parse_github_remote", MagicMock(return_value=("acme", "repo")))
    _bind(svc, "_call_merge_api", AsyncMock(return_value=_fake_response(200)))
    _bind(svc, "_delete_pr_branch_best_effort", AsyncMock())
    _bind(svc, "_sync_target_branch", AsyncMock(return_value="abc"))

    with _patch_project_service(fake_project):
        await svc.pr_merge(11, target="feature/backend/parent", project_id=project_id)

    # Two SELECTs: PR lookup (no lock) + parent lock (FOR UPDATE).
    assert session.execute.await_count == _EXPECTED_SELECT_CALLS
    parent_call = session.execute.await_args_list[1]
    parent_stmt = parent_call.args[0]
    # SQLAlchemy's compiled SELECT with for_update has `_for_update_arg` set.
    # Read via getattr so mypy doesn't trip on the protected attr name and
    # we don't need a `# type: ignore` escape hatch.
    assert getattr(parent_stmt, "_for_update_arg", None) is not None


# ---------------------------------------------------------------------------
# Scenario: Root-PR merge (parent_task_id is None) — no parent lock attempt,
# but merge still proceeds. Tests the "no parent" branch of the lock helper.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pr_merge_skips_parent_lock_for_root_task() -> None:
    project_id = uuid4()
    fake_task = MagicMock(
        id=uuid4(),
        project_id=project_id,
        parent_task_id=None,  # root task — merging into master
        assigned_to=uuid4(),
        work_session_id=None,
    )
    fake_project = MagicMock(slug="roboco")

    # No second execute() — root task has no parent to lock.
    session = MagicMock()
    pr_result = MagicMock()
    pr_result.scalar_one_or_none.return_value = fake_task
    session.execute = AsyncMock(return_value=pr_result)
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    session.flush = AsyncMock()

    svc = GitService(session)
    _bind(svc, "get_workspace", AsyncMock(return_value=Path("/tmp/ws")))
    _bind(svc, "_get_project_token_or_raise", AsyncMock(return_value="tok"))
    _bind(svc, "_parse_github_remote", MagicMock(return_value=("acme", "repo")))
    _bind(svc, "_call_merge_api", AsyncMock(return_value=_fake_response(200)))
    _bind(svc, "_delete_pr_branch_best_effort", AsyncMock())
    _bind(svc, "_sync_target_branch", AsyncMock(return_value="abc"))

    with _patch_project_service(fake_project):
        out = await svc.pr_merge(11, target="master", project_id=project_id)

    assert out == {"merge_commit_sha": "abc"}
    # Only the PR-lookup SELECT runs — no second SELECT for parent lock.
    assert session.execute.await_count == 1


# ---------------------------------------------------------------------------
# Regression: PR numbers are per-repo on GitHub but stored globally in
# tasks.pr_number with no uniqueness/repo scoping. Two tasks on different
# projects/repos can share a pr_number, so a bare `where(pr_number ==
# X).limit(1)` resolves non-deterministically — it merged the wrong repo's
# PR and marked the WRONG task's work session merged, leaving the real
# task's session `pr_status="open"` so `complete()` rejected (returned
# None) and the cell PM 500'd on `t.status` and thrashed. The task lookup
# MUST be scoped by the caller's project_id (mirrors close_pull_request).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pr_merge_scopes_task_lookup_by_project_id() -> None:
    project_id = uuid4()
    fake_task = MagicMock(
        id=uuid4(),
        project_id=project_id,
        parent_task_id=None,  # root — no parent lock SELECT
        assigned_to=uuid4(),
        work_session_id=None,
    )
    fake_project = MagicMock(slug="roboco")

    session = MagicMock()
    pr_result = MagicMock()
    pr_result.scalar_one_or_none.return_value = fake_task
    session.execute = AsyncMock(return_value=pr_result)
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    session.flush = AsyncMock()

    svc = GitService(session)
    _bind(svc, "get_workspace", AsyncMock(return_value=Path("/tmp/ws")))
    _bind(svc, "_get_project_token_or_raise", AsyncMock(return_value="tok"))
    _bind(svc, "_parse_github_remote", MagicMock(return_value=("acme", "repo")))
    _bind(svc, "_call_merge_api", AsyncMock(return_value=_fake_response(200)))
    _bind(svc, "_delete_pr_branch_best_effort", AsyncMock())
    _bind(svc, "_sync_target_branch", AsyncMock(return_value="sha"))

    with _patch_project_service(fake_project):
        await svc.pr_merge(11, target="feature/x", project_id=project_id)

    lookup_stmt = session.execute.await_args_list[0].args[0]
    sql = str(lookup_stmt.compile(compile_kwargs={"literal_binds": True}))
    assert "pr_number" in sql
    assert "project_id" in sql
    # The scoped project_id value is bound into the WHERE (Postgres renders
    # UUID literals without hyphens), not just the column name.
    assert project_id.hex in sql
