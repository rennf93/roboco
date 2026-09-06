"""Tests for `pr_merge` concurrency hardening.

Two PMs completing different subtasks of the same parent could race on
the gh API merge call. The fix is to:

1. Take a row-level lock on the parent task before invoking the merge.
2. Retry once if GitHub returns 409 (merge conflict from racing merges) -
   a pure GitHub-side recheck, no local git I/O.

These are unit tests, concurrency is exercised via mocks (status code
sequence + assertions on `with_for_update` use), not real DB transactions.
The lock-acquisition path is asserted by checking that the parent-task
SELECT statement passed to `session.execute` carries `FOR UPDATE` semantics.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from roboco.exceptions import GitError, MergeConflictError
from roboco.services.forge import RepoRef
from roboco.services.git import GitService

# Module-level constants kept local so the assertions stay readable and
# ruff's PLR2004 magic-value rule has nothing to complain about. The
# threshold mirrors httpx's `Response.is_success` rule (status < 400).
# The expected-call counters document the retry contract: at most two
# merge attempts, one `_sync_target_branch` call (post-success only, the
# 409 retry no longer syncs locally), two SELECTs (PR lookup + parent
# FOR UPDATE).
_HTTP_OK_THRESHOLD = 400
_EXPECTED_MERGE_ATTEMPTS = 2
_EXPECTED_SYNC_CALLS = 1
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
# Scenario: GitHub returns 409 once, then 200. Retry must succeed without any
# local git I/O between attempts (the merge PUT carries no local state).
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
    _bind(svc, "_parse_github_remote", MagicMock(return_value=RepoRef("acme", "repo")))

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
    # _call_merge_api invoked twice: once 409, once 200.
    assert call_seq.await_count == _EXPECTED_MERGE_ATTEMPTS
    # _sync_target_branch invoked once: the post-success refresh that
    # returns the merge SHA. No sync happens on the 409 retry itself.
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
    _bind(svc, "_parse_github_remote", MagicMock(return_value=RepoRef("acme", "repo")))

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
    _bind(svc, "_parse_github_remote", MagicMock(return_value=RepoRef("acme", "repo")))

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
    _bind(svc, "_parse_github_remote", MagicMock(return_value=RepoRef("acme", "repo")))
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
# Regression: the parent-row FOR UPDATE lock must be released (session
# commit) BEFORE the cosmetic local target-branch sync (checkout/reset,
# chown-heavy git I/O) runs. Holding it across that starved sibling merges
# on the same parent into lock_timeout on a slow workspace volume.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pr_merge_commits_before_local_target_branch_sync() -> None:
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
    committed_before_sync: list[bool] = []

    async def _sync_target_branch(*_a: object, **_k: object) -> str:
        committed_before_sync.append(session.commit.await_count > 0)
        return "sha"

    svc = GitService(session)
    _bind(svc, "get_workspace", AsyncMock(return_value=Path("/tmp/ws")))
    _bind(svc, "_get_project_token_or_raise", AsyncMock(return_value="tok"))
    _bind(svc, "_parse_github_remote", MagicMock(return_value=RepoRef("acme", "repo")))
    _bind(svc, "_call_merge_api", AsyncMock(return_value=_fake_response(200)))
    _bind(svc, "_delete_pr_branch_best_effort", AsyncMock())
    _bind(svc, "_sync_target_branch", AsyncMock(side_effect=_sync_target_branch))

    with _patch_project_service(fake_project):
        out = await svc.pr_merge(
            11, target="feature/backend/parent", project_id=project_id
        )

    assert out == {"merge_commit_sha": "sha"}
    # The local sync ran exactly once, and the lock-releasing commit had
    # already happened by the time it started.
    assert committed_before_sync == [True]
    session.commit.assert_awaited()


# ---------------------------------------------------------------------------
# Regression: releasing the parent-row lock early means two sibling
# `pr_merge` calls resolving the SAME workspace (a cell PM completing two
# leaves) can now reach the post-merge git sync concurrently, since PMs are
# exempt from one-task-at-a-time claim serialization. Without an in-process
# lock keyed by workspace path, their checkout/fetch/reset --hard sequences
# would interleave on one directory.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pr_merge_serializes_git_sync_for_same_workspace() -> None:
    workspace = Path("/tmp/ws-shared")
    order: list[tuple[str, str]] = []

    async def _one_merge(label: str) -> dict[str, Any]:
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
        # Bound directly per-instance rather than patching the module-level
        # `get_project_service`: two of these run concurrently below, and a
        # `with patch(...)` around each would race on that shared global.
        _bind(svc, "_project_for_task", AsyncMock(return_value=fake_project))
        _bind(svc, "_project_default_branch", AsyncMock(return_value="master"))
        _bind(svc, "get_workspace", AsyncMock(return_value=workspace))
        _bind(svc, "_get_project_token_or_raise", AsyncMock(return_value="tok"))
        _bind(
            svc, "_parse_github_remote", MagicMock(return_value=RepoRef("acme", "repo"))
        )
        _bind(svc, "_call_merge_api", AsyncMock(return_value=_fake_response(200)))
        _bind(svc, "_delete_pr_branch_best_effort", AsyncMock())

        async def _run_git(
            _workspace: Path, args: list[str], **_kwargs: object
        ) -> MagicMock:
            order.append((label, "enter"))
            await asyncio.sleep(0.005)
            order.append((label, "exit"))
            result = MagicMock()
            result.returncode = 0
            result.stdout = "deadbeef" if args[:2] == ["log", "-1"] else ""
            result.stderr = ""
            return result

        _bind(svc, "_run_git", AsyncMock(side_effect=_run_git))
        return await svc.pr_merge(
            11, target="feature/backend/parent", project_id=project_id
        )

    results = await asyncio.gather(_one_merge("a"), _one_merge("b"))

    assert {r["merge_commit_sha"] for r in results} == {"deadbeef"}
    # No interleaving: an "enter" for one label never appears while the
    # other label's git sequence is still in flight.
    active: set[str] = set()
    for label, kind in order:
        if kind == "enter":
            assert not active, f"{label} started while {active} was in flight"
            active.add(label)
        else:
            active.discard(label)


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
    _bind(svc, "_parse_github_remote", MagicMock(return_value=RepoRef("acme", "repo")))
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
    _bind(svc, "_parse_github_remote", MagicMock(return_value=RepoRef("acme", "repo")))
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


# ---------------------------------------------------------------------------
# #108: _merge_with_retry 405 method-not-allowed fallback.
# ---------------------------------------------------------------------------

_HTTP_METHOD_NOT_ALLOWED = 405


def _merge_ctx(pr_number: int = 11) -> GitService._MergeContext:
    return GitService._MergeContext(
        repo_ref=RepoRef("acme", "repo"),
        pr_number=pr_number,
        git_token="tok",
        workspace=Path("/tmp/ws"),
        target="feature/x",
    )


@pytest.mark.asyncio
async def test_merge_with_retry_falls_back_on_405_method_not_allowed() -> None:
    """#108: _merge_with_retry hardcoded 'squash' and raised MergeConflictError
    on a 405 (repo disallows squash) with no method fallback — wedging the PM
    on an open, mergeable PR whose repo merely has the squash button off. The
    CEO merge_pull_request path already falls back to a permitted method; the
    agent path must too. A 405 on squash → retry once with a permitted method.
    """
    svc = GitService(_make_session(MagicMock(), MagicMock))
    _bind(svc, "log", MagicMock())
    call_seq = AsyncMock(
        side_effect=[_fake_response(_HTTP_METHOD_NOT_ALLOWED), _fake_response(200)]
    )
    _bind(svc, "_call_merge_api", call_seq)
    first_method = AsyncMock(return_value="merge")
    _bind(svc, "_first_allowed_merge_method", first_method)
    pr_is_merged = AsyncMock(return_value=False)
    _bind(svc, "_pr_is_merged", pr_is_merged)
    _bind(svc, "_sync_target_branch", AsyncMock())

    resp = await svc._merge_with_retry(_merge_ctx())

    assert resp.is_success
    # Two merge attempts: squash (405) then the permitted fallback (200).
    assert call_seq.await_count == _EXPECTED_MERGE_ATTEMPTS
    # The fallback method was looked up (excluding the refused squash).
    first_method.assert_awaited_once()
    first_call = first_method.await_args
    assert first_call is not None
    assert first_call.kwargs["exclude"] == "squash"
    # An already-merged disambiguation must NOT be consulted — the 405 was
    # method-not-allowed, resolved by retry, not an already-merged PR.
    pr_is_merged.assert_not_awaited()


@pytest.mark.asyncio
async def test_merge_with_retry_raises_when_no_permitted_fallback_on_405() -> None:
    """A 405 with no permitted fallback method (every merge button off) is a
    real refusal — raise MergeConflictError, don't loop or mask it. The
    already-merged disambiguation still runs first so a 405-on-already-merged
    PR stays idempotent success."""
    svc = GitService(_make_session(MagicMock(), MagicMock))
    _bind(svc, "log", MagicMock())
    call_seq = AsyncMock(side_effect=[_fake_response(_HTTP_METHOD_NOT_ALLOWED)])
    _bind(svc, "_call_merge_api", call_seq)
    _bind(svc, "_first_allowed_merge_method", AsyncMock(return_value=None))
    pr_is_merged = AsyncMock(return_value=False)
    _bind(svc, "_pr_is_merged", pr_is_merged)
    _bind(svc, "_sync_target_branch", AsyncMock())

    with pytest.raises(MergeConflictError):
        await svc._merge_with_retry(_merge_ctx())

    # Only the initial squash attempt — no fallback retry when none permitted.
    assert call_seq.await_count == 1
    # Already-merged disambiguation ran (the 405 could also mean already-merged).
    pr_is_merged.assert_awaited_once()
