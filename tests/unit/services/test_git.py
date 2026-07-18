"""Unit tests for GitService gateway-backfill methods.

These tests target signature-level behavior. Full integration with the
GitHub REST API + filesystem lives under integration tests; here we
mock the network and filesystem boundaries.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import httpx
import pytest
from roboco.config import settings
from roboco.exceptions import GitCommandError, GitError, MergeConflictError
from roboco.services.base import NotFoundError, UnauthorizedError, ValidationError
from roboco.services.git import GitService

if TYPE_CHECKING:
    from contextlib import AbstractContextManager

_EXPECTED_PR_NUMBER = 7
_PUSHED_COMMIT_COUNT = 2


def _make_session(execute_returns: object | None = None) -> MagicMock:
    """Build a MagicMock-backed session with execute pre-stubbed."""
    session = MagicMock()
    session.execute = AsyncMock(return_value=execute_returns)
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    session.flush = AsyncMock()
    return session


def _service(execute_returns: object | None = None) -> GitService:
    return GitService(_make_session(execute_returns))


def _patch_project_service(project: object | None) -> AbstractContextManager[object]:
    """Patch get_project_service to return a service whose .get() resolves project."""
    fake_service = MagicMock()
    fake_service.get = AsyncMock(return_value=project)
    fake_service.get_by_slug = AsyncMock(return_value=project)
    return patch("roboco.services.git.get_project_service", return_value=fake_service)


def _bind(svc: GitService, name: str, value: object) -> None:
    """Stub `name` on `svc` without tripping mypy's method-assign check.

    Uses setattr so the attribute lookup is dynamic (vs. attribute
    binding to the class), letting tests override async helpers
    without triggering [method-assign].
    """
    object.__setattr__(svc, name, value)


# ---------------------------------------------------------------------------
# _task_for_branch + _project_slug_for_branch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_task_for_branch_returns_task_when_present() -> None:
    fake_task = MagicMock(branch_name="feature/backend/abc12345")
    result = MagicMock()
    result.scalar_one_or_none.return_value = fake_task
    svc = _service(execute_returns=result)
    out = await svc._task_for_branch("feature/backend/abc12345")
    assert out is fake_task


@pytest.mark.asyncio
async def test_task_for_branch_returns_none_when_missing() -> None:
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    svc = _service(execute_returns=result)
    assert await svc._task_for_branch("nope/branch") is None


@pytest.mark.asyncio
async def test_project_slug_for_branch_returns_slug() -> None:
    project_id = uuid4()
    fake_task = MagicMock(branch_name="feature/backend/x", project_id=project_id)
    fake_project = MagicMock(slug="roboco")
    svc = _service()
    _bind(svc, "_task_for_branch", AsyncMock(return_value=fake_task))
    with _patch_project_service(fake_project):
        out = await svc._project_slug_for_branch("feature/backend/x")
    assert out == "roboco"


@pytest.mark.asyncio
async def test_project_slug_for_branch_none_when_no_task() -> None:
    svc = _service()
    _bind(svc, "_task_for_branch", AsyncMock(return_value=None))
    assert await svc._project_slug_for_branch("missing") is None


@pytest.mark.asyncio
async def test_project_for_task_resolves_coordination_root_via_product() -> None:
    """A project-less coordination root resolves its repo from the product map."""
    pid = uuid4()
    fake_project = MagicMock(slug="roboco")
    task = MagicMock(project_id=None, product_id=uuid4())
    svc = _service()
    product_svc = MagicMock(distinct_project_ids=AsyncMock(return_value=[pid]))
    with (
        _patch_project_service(fake_project),
        patch("roboco.services.product.get_product_service", return_value=product_svc),
    ):
        out = await svc._project_for_task(task)
    assert out is fake_project
    product_svc.distinct_project_ids.assert_awaited_once()


@pytest.mark.asyncio
async def test_project_for_task_uses_project_id_when_present() -> None:
    """A normal task resolves by project_id exactly as before (additive change)."""
    fake_project = MagicMock(slug="roboco")
    task = MagicMock(project_id=uuid4(), product_id=None)
    svc = _service()
    with _patch_project_service(fake_project):
        out = await svc._project_for_task(task)
    assert out is fake_project


# ---------------------------------------------------------------------------
# push_task_branch: idempotent push at the QA-submission boundary
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_push_task_branch_pushes_task_branch_by_name() -> None:
    """Pushes the task's branch BY NAME (independent of the current checkout).

    A dev's clone is shared across many tasks, so by the QA-submission /
    open_pr boundary it is usually parked on a LATER task's branch. The old
    assert-on-current-branch gate rejected the push and the locally-committed
    work never reached origin; the push must target the named ref instead.
    """
    task = MagicMock(branch_name="feature/backend/abc")
    project = MagicMock(slug="roboco")
    svc = _service()
    _bind(svc, "_assert_task_owned_with_branch", AsyncMock(return_value=task))
    _bind(svc, "_project_for_task", AsyncMock(return_value=project))
    _bind(svc, "get_workspace", AsyncMock(return_value=Path("/tmp/ws")))
    assert_branch = AsyncMock()
    _bind(svc, "_assert_on_task_branch", assert_branch)
    push_mock = AsyncMock(return_value=("feature/backend/abc", _PUSHED_COMMIT_COUNT))
    _bind(svc, "push", push_mock)

    pushed = await svc.push_task_branch(uuid4(), uuid4())

    assert pushed == _PUSHED_COMMIT_COUNT
    push_mock.assert_awaited_once_with(Path("/tmp/ws"), branch="feature/backend/abc")
    # The old current-branch gate is no longer consulted on the push path.
    assert_branch.assert_not_awaited()


@pytest.mark.asyncio
async def test_push_branch_pushes_named_branch_not_current_checkout() -> None:
    """push_branch (open_pr's push side effect) pushes the NAMED branch.

    The clone root is shared across a dev's tasks and (F123) parked on the
    default branch while the task branch lives in a per-task worktree.
    push_branch used to call push(workspace) with no ``branch`` arg, so
    push() fell back to get_current_branch(workspace) and pushed the wrong
    ref (the clone root's checkout, e.g. the default branch). The dev's
    commit then never reached origin, create_pr 422'd with "No commits
    between", and the dev was forced into i_am_blocked — stranded work the
    PM's unblock cannot repair (it flips status, not git state). The named
    branch must be passed through to push().
    """
    branch_name = "feature/backend/fb836f80--03f80432--d3dab0fc--b04afcb5"
    svc = _service()
    _bind(svc, "_workspace_for_branch", AsyncMock(return_value=Path("/tmp/ws")))
    push_mock = AsyncMock(return_value=(branch_name, _PUSHED_COMMIT_COUNT))
    _bind(svc, "push", push_mock)

    result_branch, pushed = await svc.push_branch(branch_name)

    assert result_branch == branch_name
    assert pushed == _PUSHED_COMMIT_COUNT
    # The named branch is forwarded to push() — NOT push(workspace) which
    # would default to the clone root's current checkout.
    push_mock.assert_awaited_once_with(Path("/tmp/ws"), branch=branch_name)


@pytest.mark.asyncio
async def test_push_targets_explicit_branch_not_current_checkout() -> None:
    """push(branch=X) pushes X by ref even when the workspace is on Y."""
    svc = _service()
    _bind(svc, "get_current_branch", AsyncMock(return_value="feature/frontend/OTHER"))
    _bind(svc, "_token_for_workspace", AsyncMock(return_value=None))
    calls: list[list[str]] = []

    async def _run_git(_ws: object, args: list[str], **_kw: object) -> MagicMock:
        calls.append(args)
        res = MagicMock()
        res.returncode = 0
        res.stdout = "3" if args[:2] == ["rev-list", "--count"] else ""
        return res

    _bind(svc, "_run_git", AsyncMock(side_effect=_run_git))

    branch, _pushed = await svc.push(Path("/tmp/ws"), branch="feature/frontend/TASK")

    assert branch == "feature/frontend/TASK"
    push_args = next(a for a in calls if a and a[0] == "push")
    assert "feature/frontend/TASK" in push_args
    assert "feature/frontend/OTHER" not in push_args


@pytest.mark.asyncio
async def test_push_recovers_missing_local_branch_from_origin() -> None:
    """A push-by-name on a re-provisioned/shared clone missing the local ref
    recovers it from origin instead of dying on "src refspec ... does not
    match any".

    The branch's commits are already on origin (pushed in a prior cycle/clone),
    so after recreating the local tracking ref the push is a clean no-op.
    """
    svc = _service()
    _bind(svc, "_token_for_workspace", AsyncMock(return_value=None))
    calls: list[list[str]] = []

    async def _run_git(_ws: object, args: list[str], **_kw: object) -> MagicMock:
        calls.append(args)
        res = MagicMock()
        # Local ref MISSING; origin HAS it.
        if args[:2] == ["rev-parse", "--verify"]:
            is_local = any(a.startswith("refs/heads/") for a in args)
            res.returncode = 1 if is_local else 0
            res.stdout = ""
            return res
        res.returncode = 0
        res.stdout = "0" if args[:2] == ["rev-list", "--count"] else ""
        return res

    _bind(svc, "_run_git", AsyncMock(side_effect=_run_git))

    branch, _pushed = await svc.push(Path("/tmp/ws"), branch="feature/backend/TASK")

    assert branch == "feature/backend/TASK"
    # It fetched origin and recreated the local ref before pushing.
    assert ["fetch", "origin", "feature/backend/TASK"] in calls
    assert ["branch", "feature/backend/TASK", "origin/feature/backend/TASK"] in calls
    assert any(a and a[0] == "push" for a in calls)


@pytest.mark.asyncio
async def test_push_fails_loud_when_branch_absent_local_and_origin() -> None:
    """When the named branch is in neither the local clone nor origin, the work
    is genuinely lost from this clone — fail with a recoverable instruction, not
    the raw "src refspec does not match any"."""
    svc = _service()
    _bind(svc, "_token_for_workspace", AsyncMock(return_value=None))

    async def _run_git(_ws: object, args: list[str], **_kw: object) -> MagicMock:
        res = MagicMock()
        if args[:2] == ["rev-parse", "--verify"]:
            res.returncode = 1  # absent both locally and on origin
            res.stdout = ""
            return res
        res.returncode = 0
        res.stdout = ""
        return res

    _bind(svc, "_run_git", AsyncMock(side_effect=_run_git))

    with pytest.raises(GitCommandError, match="unclaim the task and"):
        await svc.push(Path("/tmp/ws"), branch="feature/backend/GONE")


@pytest.mark.asyncio
async def test_push_force_uses_force_with_lease_not_bare_force() -> None:
    """push(force=True) must use --force-with-lease, not bare --force.

    Bare --force silently overwrites a concurrent remote advance; --force-with-lease
    fails fast instead of clobbering someone else's commits.
    """
    svc = _service()
    _bind(svc, "_token_for_workspace", AsyncMock(return_value=None))
    calls: list[list[str]] = []

    async def _run_git(_ws: object, args: list[str], **_kw: object) -> MagicMock:
        calls.append(args)
        res = MagicMock()
        res.returncode = 0
        res.stdout = "0" if args[:2] == ["rev-list", "--count"] else ""
        return res

    _bind(svc, "_run_git", AsyncMock(side_effect=_run_git))

    await svc.push(Path("/tmp/ws"), force=True, branch="feature/backend/TASK")

    push_args = next(a for a in calls if a and a[0] == "push")
    assert "--force-with-lease" in push_args
    assert "--force" not in push_args


@pytest.mark.asyncio
async def test_push_no_force_has_neither_flag() -> None:
    """push(force=False) carries neither --force nor --force-with-lease."""
    svc = _service()
    _bind(svc, "_token_for_workspace", AsyncMock(return_value=None))
    calls: list[list[str]] = []

    async def _run_git(_ws: object, args: list[str], **_kw: object) -> MagicMock:
        calls.append(args)
        res = MagicMock()
        res.returncode = 0
        res.stdout = "0" if args[:2] == ["rev-list", "--count"] else ""
        return res

    _bind(svc, "_run_git", AsyncMock(side_effect=_run_git))

    await svc.push(Path("/tmp/ws"), force=False, branch="feature/backend/TASK")

    push_args = next(a for a in calls if a and a[0] == "push")
    assert "--force-with-lease" not in push_args
    assert "--force" not in push_args


@pytest.mark.asyncio
async def test_pr_head_is_task_branch_not_current() -> None:
    """The PR head is the task's recorded branch, not the workspace checkout."""
    task = MagicMock(branch_name="feature/frontend/TASK")
    svc = _service()
    _bind(svc, "get_current_branch", AsyncMock(return_value="feature/frontend/OTHER"))
    req = MagicMock(task_id=uuid4())
    with patch("roboco.services.git.get_task_service") as gts:
        gts.return_value.get = AsyncMock(return_value=task)
        head = await svc._pr_head_branch(Path("/tmp/ws"), req)
    assert head == "feature/frontend/TASK"


@pytest.mark.asyncio
async def test_pr_head_falls_back_to_current_when_no_task() -> None:
    """No task_id → the PR head is the current checkout (unchanged behavior)."""
    svc = _service()
    _bind(svc, "get_current_branch", AsyncMock(return_value="feature/frontend/CUR"))
    req = MagicMock(task_id=None)
    head = await svc._pr_head_branch(Path("/tmp/ws"), req)
    assert head == "feature/frontend/CUR"


@pytest.mark.asyncio
async def test_push_task_branch_noop_for_project_less_task() -> None:
    """A git-exempt task (no resolvable project) is a no-op, not an error."""
    task = MagicMock(branch_name="feature/main_pm/abc")
    svc = _service()
    _bind(svc, "_assert_task_owned_with_branch", AsyncMock(return_value=task))
    _bind(svc, "_project_for_task", AsyncMock(return_value=None))
    push_mock = AsyncMock()
    _bind(svc, "push", push_mock)

    pushed = await svc.push_task_branch(uuid4(), uuid4())

    assert pushed == 0
    push_mock.assert_not_awaited()


# ---------------------------------------------------------------------------
# diff: derives parent + invokes git diff
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_diff_returns_diff_stdout() -> None:
    svc = _service()
    _bind(svc, "_workspace_for_branch", AsyncMock(return_value=Path("/tmp/ws")))

    async def _run_git(
        _workspace: Path,
        args: list[str],
        check: bool = True,
        token: str | None = None,
    ) -> MagicMock:
        del check, token
        if args[:1] == ["fetch"]:
            return MagicMock(stdout="", returncode=0)
        return MagicMock(stdout="diff --git a b\n+hello\n", returncode=0)

    _bind(svc, "_run_git", AsyncMock(side_effect=_run_git))
    out = await svc.diff(branch_name="feature/backend/abc")
    assert "+hello" in out


@pytest.mark.asyncio
async def test_read_file_at_branch_returns_committed_content() -> None:
    svc = _service()
    _bind(svc, "_workspace_for_branch", AsyncMock(return_value=Path("/tmp/ws")))
    _bind(svc, "_token_for_branch", AsyncMock(return_value=None))
    _bind(svc, "_resolve_head_ref", AsyncMock(return_value="HEAD"))
    _bind(
        svc,
        "_run_git",
        AsyncMock(return_value=MagicMock(stdout="# API\nbody\n", returncode=0)),
    )
    out = await svc.read_file_at_branch(
        branch_name="feature/backend/abc", path="docs/api.md"
    )
    assert out == "# API\nbody\n"


@pytest.mark.asyncio
async def test_read_file_at_branch_missing_returns_none() -> None:
    svc = _service()
    _bind(svc, "_workspace_for_branch", AsyncMock(return_value=Path("/tmp/ws")))
    _bind(svc, "_token_for_branch", AsyncMock(return_value=None))
    _bind(svc, "_resolve_head_ref", AsyncMock(return_value="HEAD"))
    # git show on a path that isn't in the tree exits non-zero.
    _bind(
        svc,
        "_run_git",
        AsyncMock(return_value=MagicMock(stdout="", returncode=128)),
    )
    out = await svc.read_file_at_branch(
        branch_name="feature/backend/abc", path="nope.md"
    )
    assert out is None


# ---------------------------------------------------------------------------
# pr_target: GitHub round-trip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pr_target_returns_base_ref() -> None:
    project_id = uuid4()
    fake_task = MagicMock(project_id=project_id, assigned_to=uuid4())
    fake_project = MagicMock(slug="roboco")
    result = MagicMock()
    result.scalar_one_or_none.return_value = fake_task

    svc = _service(execute_returns=result)
    _bind(svc, "get_workspace", AsyncMock(return_value=Path("/tmp/ws")))
    _bind(svc, "_parse_github_remote", MagicMock(return_value=("acme", "repo")))
    _bind(svc, "_get_project_token_or_raise", AsyncMock(return_value="token"))

    fake_response = MagicMock()
    fake_response.is_success = True
    fake_response.json.return_value = {"base": {"ref": "feature/parent"}}
    fake_client = MagicMock()
    fake_client.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client.__aexit__ = AsyncMock(return_value=False)
    fake_client.get = AsyncMock(return_value=fake_response)

    with (
        _patch_project_service(fake_project),
        patch("roboco.services.git.httpx.AsyncClient", return_value=fake_client),
    ):
        out = await svc.pr_target(42, project_id=project_id)
    assert out == "feature/parent"


@pytest.mark.asyncio
async def test_pr_target_raises_when_pr_not_found() -> None:
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    svc = _service(execute_returns=result)
    with pytest.raises(NotFoundError):
        await svc.pr_target(99, project_id=uuid4())


# ---------------------------------------------------------------------------
# create_pr: parses response and stores PR
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_pr_returns_pr_dict() -> None:
    project_id = uuid4()
    fake_task = MagicMock(
        id=uuid4(),
        project_id=project_id,
        assigned_to=uuid4(),
        title="Add login",
        description="A short description",
    )
    fake_project = MagicMock(slug="roboco")
    svc = _service()
    _bind(svc, "_task_for_branch", AsyncMock(return_value=fake_task))
    _bind(svc, "_workspace_for_branch", AsyncMock(return_value=Path("/tmp/ws")))
    _bind(svc, "_get_project_token_or_raise", AsyncMock(return_value="tok"))
    _bind(svc, "_parse_github_remote", MagicMock(return_value=("acme", "repo")))
    _bind(svc, "_record_pr_atomically", AsyncMock())
    # parent == default → _ensure_base_on_remote short-circuits (no git call)
    _bind(svc, "_project_default_branch", AsyncMock(return_value="master"))

    fake_resp = MagicMock()
    fake_resp.is_success = True
    fake_resp.status_code = 201
    fake_resp.json.return_value = {
        "number": _EXPECTED_PR_NUMBER,
        "html_url": f"https://github.com/acme/repo/pull/{_EXPECTED_PR_NUMBER}",
    }
    _bind(svc, "_post_pr", AsyncMock(return_value=fake_resp))
    _bind(svc, "_apply_pr_labels", AsyncMock())

    with _patch_project_service(fake_project):
        out = await svc.create_pr(
            "feature/backend/abc12345", parent="master", is_root_pr=True
        )
    assert out["pr_number"] == _EXPECTED_PR_NUMBER
    assert "github.com" in out["pr_url"]
    assert out["is_root_pr"] is True


@pytest.mark.asyncio
async def test_create_pr_records_pr_despite_cancellation_after_post() -> None:
    """A cancellation landing after the GitHub POST succeeds but before the
    local record commits must not lose the record: asyncio.shield lets
    _record_pr_atomically finish, the cancellation still propagates."""
    project_id = uuid4()
    fake_task = MagicMock(
        id=uuid4(),
        project_id=project_id,
        assigned_to=uuid4(),
        title="Add login",
        description="A short description",
    )
    fake_project = MagicMock(slug="roboco")
    svc = _service()
    _bind(svc, "_task_for_branch", AsyncMock(return_value=fake_task))
    _bind(svc, "_workspace_for_branch", AsyncMock(return_value=Path("/tmp/ws")))
    _bind(svc, "_get_project_token_or_raise", AsyncMock(return_value="tok"))
    _bind(svc, "_parse_github_remote", MagicMock(return_value=("acme", "repo")))
    _bind(svc, "_project_default_branch", AsyncMock(return_value="master"))

    recorded = {"done": False}

    async def _slow_record(*_args: object, **_kwargs: object) -> None:
        await asyncio.sleep(0.05)
        recorded["done"] = True

    _bind(svc, "_record_pr_atomically", _slow_record)

    fake_resp = MagicMock()
    fake_resp.is_success = True
    fake_resp.status_code = 201
    fake_resp.json.return_value = {
        "number": _EXPECTED_PR_NUMBER,
        "html_url": f"https://github.com/acme/repo/pull/{_EXPECTED_PR_NUMBER}",
    }
    _bind(svc, "_post_pr", AsyncMock(return_value=fake_resp))
    _bind(svc, "_apply_pr_labels", AsyncMock())

    with _patch_project_service(fake_project):
        task = asyncio.ensure_future(
            svc.create_pr("feature/backend/abc12345", parent="master", is_root_pr=True)
        )
        await asyncio.sleep(0.01)  # let create_pr reach the shielded await
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    # _await_shielded waits the in-flight record out BEFORE re-raising, so
    # by the time `await task` raised, the record had already completed.
    assert recorded["done"] is True, (
        "shield must let the record finish despite cancellation"
    )


@pytest.mark.asyncio
async def test_create_pr_cancellation_waits_out_record_before_reraising() -> None:
    """Ordering guard for _await_shielded: on cancellation the in-flight
    _record_pr_atomically must run to COMPLETION before CancelledError
    re-raises to the caller. A bare asyncio.shield detaches the write and
    re-raises immediately — the write then races get_db's rollback on the
    same AsyncSession and asyncpg raises InterfaceError ('another operation
    is in progress'), escaping as a 500 instead of the middleware's 504."""
    project_id = uuid4()
    fake_task = MagicMock(
        id=uuid4(),
        project_id=project_id,
        assigned_to=uuid4(),
        title="Add login",
        description="A short description",
    )
    fake_project = MagicMock(slug="roboco")
    svc = _service()
    _bind(svc, "_task_for_branch", AsyncMock(return_value=fake_task))
    _bind(svc, "_workspace_for_branch", AsyncMock(return_value=Path("/tmp/ws")))
    _bind(svc, "_get_project_token_or_raise", AsyncMock(return_value="tok"))
    _bind(svc, "_parse_github_remote", MagicMock(return_value=("acme", "repo")))
    _bind(svc, "_project_default_branch", AsyncMock(return_value="master"))

    order: list[str] = []

    async def _slow_record(*_args: object, **_kwargs: object) -> None:
        await asyncio.sleep(0.05)
        order.append("record_done")

    _bind(svc, "_record_pr_atomically", _slow_record)

    fake_resp = MagicMock()
    fake_resp.is_success = True
    fake_resp.status_code = 201
    fake_resp.json.return_value = {
        "number": _EXPECTED_PR_NUMBER,
        "html_url": f"https://github.com/acme/repo/pull/{_EXPECTED_PR_NUMBER}",
    }
    _bind(svc, "_post_pr", AsyncMock(return_value=fake_resp))
    _bind(svc, "_apply_pr_labels", AsyncMock())

    with _patch_project_service(fake_project):
        task = asyncio.ensure_future(
            svc.create_pr("feature/backend/abc12345", parent="master", is_root_pr=True)
        )
        await asyncio.sleep(0.01)  # mid-record: cancellation lands in the shield
        task.cancel()
        # pytest.raises re-raises any OTHER exception type (e.g. the
        # InterfaceError a racing rollback would surface), failing the test —
        # that is assertion (a): CancelledError and nothing else propagates.
        with pytest.raises(asyncio.CancelledError):
            await task
        # Appended synchronously right after propagation: no other coroutine
        # can run in between, so this marker coming SECOND proves the record
        # coroutine had already completed before the cancellation re-raised.
        order.append("cancel_raised")

    assert order == ["record_done", "cancel_raised"], (
        f"record must complete BEFORE the cancellation re-raises; got {order}"
    )


@pytest.mark.asyncio
async def test_create_pr_raises_when_branch_not_found() -> None:
    svc = _service()
    _bind(svc, "_task_for_branch", AsyncMock(return_value=None))
    with pytest.raises(NotFoundError):
        await svc.create_pr("missing/branch", parent="master", is_root_pr=False)


# ---------------------------------------------------------------------------
# _ensure_base_on_remote: create the PR base branch if it's missing on origin
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ensure_base_creates_missing_base_off_default() -> None:
    """Missing base branch is created on origin off the default branch tip."""
    svc = _service()
    _bind(svc, "_project_default_branch", AsyncMock(return_value="master"))
    calls: list[list[str]] = []

    async def fake_run_git(_ws: Path, args: list[str], **_: object) -> MagicMock:
        calls.append(args)
        if args[0] == "ls-remote":
            return MagicMock(stdout="", returncode=0, stderr="")  # base absent
        return MagicMock(stdout="", returncode=0, stderr="")

    _bind(svc, "_run_git", fake_run_git)
    out = await svc._ensure_base_on_remote(
        Path("/tmp/ws"), "feature/frontend/abc--def", "roboco", "tok"
    )
    assert out == "feature/frontend/abc--def"
    assert any(
        a[0] == "push" and a[-1] == "origin/master:refs/heads/feature/frontend/abc--def"
        for a in calls
    ), calls


@pytest.mark.asyncio
async def test_ensure_base_passthrough_when_present() -> None:
    """An existing base branch is returned unchanged with no push."""
    svc = _service()
    _bind(svc, "_project_default_branch", AsyncMock(return_value="master"))
    pushed = False

    async def fake_run_git(_ws: Path, args: list[str], **_: object) -> MagicMock:
        nonlocal pushed
        if args[0] == "push":
            pushed = True
        if args[0] == "ls-remote":
            return MagicMock(
                stdout="sha\trefs/heads/feature/x", returncode=0, stderr=""
            )
        return MagicMock(stdout="", returncode=0, stderr="")

    _bind(svc, "_run_git", fake_run_git)
    out = await svc._ensure_base_on_remote(
        Path("/tmp/ws"), "feature/x", "roboco", "tok"
    )
    assert out == "feature/x"
    assert pushed is False


@pytest.mark.asyncio
async def test_ensure_base_falls_back_to_default_when_create_fails() -> None:
    """If the create push fails, retarget to the default branch (never 422)."""
    svc = _service()
    _bind(svc, "_project_default_branch", AsyncMock(return_value="master"))

    async def fake_run_git(_ws: Path, args: list[str], **_: object) -> MagicMock:
        if args[0] == "ls-remote":
            return MagicMock(stdout="", returncode=0, stderr="")
        if args[0] == "push":
            return MagicMock(stdout="", returncode=1, stderr="denied")
        return MagicMock(stdout="", returncode=0, stderr="")

    _bind(svc, "_run_git", fake_run_git)
    out = await svc._ensure_base_on_remote(
        Path("/tmp/ws"), "feature/x", "roboco", "tok"
    )
    assert out == "master"


# ---------------------------------------------------------------------------
# _apply_pr_labels / _ensure_label_exists: never raise, even on a non-httpx
# error — labeling must not surface as a spurious tool failure after the PR
# itself was already created.
# ---------------------------------------------------------------------------


def _non_httpx_raising_client() -> MagicMock:
    """An AsyncClient whose POST raises a plain (non-httpx) exception."""
    fake_client = MagicMock()
    fake_client.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client.__aexit__ = AsyncMock(return_value=False)
    fake_client.post = AsyncMock(side_effect=RuntimeError("boom"))
    return fake_client


@pytest.mark.asyncio
async def test_ensure_label_exists_swallows_non_httpx_error() -> None:
    svc = _service()
    with patch(
        "roboco.services.git.httpx.AsyncClient",
        return_value=_non_httpx_raising_client(),
    ):
        await svc._ensure_label_exists("acme", "repo", "tok", "cell/backend")


@pytest.mark.asyncio
async def test_apply_pr_labels_swallows_non_httpx_error() -> None:
    svc = _service()
    _bind(svc, "_ensure_label_exists", AsyncMock())
    with patch(
        "roboco.services.git.httpx.AsyncClient",
        return_value=_non_httpx_raising_client(),
    ):
        await svc._apply_pr_labels("acme", "repo", "tok", 11, ["cell/backend"])


# ---------------------------------------------------------------------------
# pr_merge: returns merge commit dict
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pr_merge_returns_merge_commit_dict() -> None:
    project_id = uuid4()
    fake_task = MagicMock(
        project_id=project_id,
        # Root task — no parent to lock; concurrency tests cover the
        # parent-lock + retry-on-409 paths separately.
        parent_task_id=None,
        assigned_to=uuid4(),
        work_session_id=None,
    )
    fake_project = MagicMock(slug="roboco")
    result = MagicMock()
    result.scalar_one_or_none.return_value = fake_task

    svc = _service(execute_returns=result)
    _bind(svc, "get_workspace", AsyncMock(return_value=Path("/tmp/ws")))
    _bind(svc, "_get_project_token_or_raise", AsyncMock(return_value="tok"))
    _bind(svc, "_parse_github_remote", MagicMock(return_value=("acme", "repo")))

    fake_resp = MagicMock(is_success=True, status_code=200)
    _bind(svc, "_call_merge_api", AsyncMock(return_value=fake_resp))
    _bind(svc, "_delete_pr_branch_best_effort", AsyncMock())
    _bind(svc, "_sync_target_branch", AsyncMock(return_value="abc123sha"))
    _bind(svc, "_project_default_branch", AsyncMock(return_value="master"))

    # Merges flow UP the chain (cell -> Main-PM branch), never into master via
    # this agent path — target is the integration branch, not the default branch.
    with _patch_project_service(fake_project):
        out = await svc.pr_merge(
            11, target="feature/main_pm/root1234", project_id=project_id
        )
    assert out == {"merge_commit_sha": "abc123sha"}


@pytest.mark.asyncio
async def test_pr_merge_into_default_branch_is_ceo_only() -> None:
    """The agent merge path refuses to merge into a repo's default branch."""
    project_id = uuid4()
    fake_task = MagicMock(
        project_id=project_id, parent_task_id=None, assigned_to=uuid4()
    )
    fake_project = MagicMock(slug="roboco")
    result = MagicMock()
    result.scalar_one_or_none.return_value = fake_task

    svc = _service(execute_returns=result)
    _bind(svc, "get_workspace", AsyncMock(return_value=Path("/tmp/ws")))
    _bind(svc, "_get_project_token_or_raise", AsyncMock(return_value="tok"))
    _bind(svc, "_parse_github_remote", MagicMock(return_value=("acme", "repo")))
    _bind(svc, "_project_default_branch", AsyncMock(return_value="master"))
    merge_api = AsyncMock()
    _bind(svc, "_call_merge_api", merge_api)

    with (
        _patch_project_service(fake_project),
        pytest.raises(UnauthorizedError, match="CEO_ONLY"),
    ):
        await svc.pr_merge(11, target="master", project_id=project_id)
    # Guard fires before any GitHub merge call.
    merge_api.assert_not_called()


# ---------------------------------------------------------------------------
# commit: stages + commits a large changeset with the longer git timeout
# (issue #13 — the panel commit verb timed out on the 30s default budget).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_commit_uses_longer_timeout_for_staging_and_commit() -> None:
    """`add`/`commit` must run with the commit-timeout, not the default.

    Large multi-file changesets exceeded the 30s default git timeout. The
    staging (`git add`) and `git commit` ops now pass
    `settings.git_commit_timeout_seconds` so big changesets don't time out.
    """
    svc = _service()
    _bind(svc, "_workspace_for_branch", AsyncMock(return_value=Path("/tmp/ws")))
    _bind(svc, "_assert_on_task_branch", AsyncMock())
    _bind(svc, "_ensure_worktree_for_commit", AsyncMock())
    _bind(svc, "_task_for_branch", AsyncMock(return_value=None))
    _bind(svc, "_parse_commit_stats", MagicMock(return_value=(1, 0, 1)))

    timeouts_by_subcmd: dict[str, int | None] = {}

    async def _run_git(
        _workspace: Path,
        args: list[str],
        check: bool = True,
        token: str | None = None,
        timeout: int | None = None,
    ) -> MagicMock:
        del check, token
        timeouts_by_subcmd[args[0]] = timeout
        if args[:2] == ["log", "-1"]:
            return MagicMock(stdout="deadbeef|feat: big change\n", returncode=0)
        return MagicMock(stdout="", returncode=0)

    _bind(svc, "_run_git", AsyncMock(side_effect=_run_git))

    out = await svc.commit(
        branch_name="feature/frontend/abc12345",
        message="implement the panel dashboard layout and routing",
        task_id=uuid4(),
    )

    assert out["sha"] == "deadbeef"
    # Staging + commit ran with the longer commit budget...
    assert timeouts_by_subcmd["add"] == settings.git_commit_timeout_seconds
    assert timeouts_by_subcmd["commit"] == settings.git_commit_timeout_seconds
    # ...while the cheap read-only ops kept the default (None → default budget).
    assert timeouts_by_subcmd["log"] is None


@pytest.mark.asyncio
async def test_push_restates_gh001_as_permanent() -> None:
    """A >100MB push rejection (GH001) is re-raised with a clear, permanent
    message that points at i_am_blocked — not the raw output an agent mis-reads
    as a transient timeout and blind-retries."""
    svc = _service()
    _bind(svc, "get_current_branch", AsyncMock(return_value="feature/x"))
    _bind(svc, "_token_for_workspace", AsyncMock(return_value=None))

    async def _run_git(_workspace: object, args: list[str], **_kw: object) -> object:
        if args[:1] == ["push"]:
            raise GitCommandError(
                "git push",
                "remote: error: GH001: large.bin is 115.00 MB; this exceeds "
                "GitHub's file size limit of 100.00 MB",
            )
        return MagicMock(returncode=0, stdout="1", stderr="")

    _bind(svc, "_run_git", AsyncMock(side_effect=_run_git))
    with pytest.raises(GitCommandError, match="i_am_blocked"):
        await svc.push(Path("/tmp/ws"))


@pytest.mark.asyncio
async def test_push_propagates_non_gh001_error_unchanged() -> None:
    """A non-size push failure is re-raised as-is (not reclassified)."""
    svc = _service()
    _bind(svc, "get_current_branch", AsyncMock(return_value="feature/x"))
    _bind(svc, "_token_for_workspace", AsyncMock(return_value=None))

    async def _run_git(_workspace: object, args: list[str], **_kw: object) -> object:
        if args[:1] == ["push"]:
            raise GitCommandError("git push", "fatal: Authentication failed")
        return MagicMock(returncode=0, stdout="1", stderr="")

    _bind(svc, "_run_git", AsyncMock(side_effect=_run_git))
    with pytest.raises(GitCommandError, match="Authentication failed"):
        await svc.push(Path("/tmp/ws"))


# ---------------------------------------------------------------------------
# _sync_target_branch fallback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_target_branch_uses_local_ref_when_present() -> None:
    """If the target branch exists locally, checkout + hard-sync to origin."""
    svc = _service()
    calls: list[list[str]] = []

    async def _run_git(_workspace: object, args: list[str], **_kw: object) -> object:
        calls.append(args)
        if args[:2] == ["checkout", "feature/backend/parent"]:
            return MagicMock(returncode=0, stdout="", stderr="")
        if args[:2] == ["log", "-1"]:
            return MagicMock(returncode=0, stdout="local-tip-sha", stderr="")
        return MagicMock(returncode=0, stdout="", stderr="")

    _bind(svc, "_run_git", AsyncMock(side_effect=_run_git))
    sha = await svc._sync_target_branch(
        Path("/tmp/ws"), "feature/backend/parent", "token"
    )
    assert sha == "local-tip-sha"
    assert ["checkout", "feature/backend/parent"] in calls
    assert ["fetch", "origin", "feature/backend/parent"] in calls
    assert ["reset", "--hard", "origin/feature/backend/parent"] in calls
    assert ["pull"] not in calls
    assert ["checkout", "-b", "feature/backend/parent"] not in [c[:3] for c in calls]


@pytest.mark.asyncio
async def test_sync_target_branch_fetches_and_tracks_when_local_ref_missing() -> None:
    """A parent branch that only exists on origin is fetched and tracked."""
    svc = _service()
    calls: list[list[str]] = []

    async def _run_git(_workspace: object, args: list[str], **_kw: object) -> object:
        calls.append(args)
        if args[:2] == ["checkout", "feature/backend/parent"]:
            return MagicMock(returncode=1, stdout="", stderr="pathspec did not match")
        if args[:2] == ["fetch", "origin"]:
            return MagicMock(returncode=0, stdout="", stderr="")
        if args[:3] == ["checkout", "-b", "feature/backend/parent"]:
            return MagicMock(returncode=0, stdout="", stderr="")
        if args[:2] == ["log", "-1"]:
            return MagicMock(returncode=0, stdout="origin-tip-sha", stderr="")
        return MagicMock(returncode=0, stdout="", stderr="")

    _bind(svc, "_run_git", AsyncMock(side_effect=_run_git))
    sha = await svc._sync_target_branch(
        Path("/tmp/ws"), "feature/backend/parent", "token"
    )
    assert sha == "origin-tip-sha"
    assert ["checkout", "feature/backend/parent"] in calls
    assert ["fetch", "origin", "feature/backend/parent"] in calls
    assert [
        "checkout",
        "-b",
        "feature/backend/parent",
        "origin/feature/backend/parent",
    ] in calls
    assert ["reset", "--hard", "origin/feature/backend/parent"] in calls
    assert ["pull"] not in calls


@pytest.mark.asyncio
async def test_sync_target_branch_raises_when_origin_also_missing() -> None:
    """If the branch is missing locally AND on origin, raise a clear GitError."""
    svc = _service()

    async def _run_git(_workspace: object, args: list[str], **_kw: object) -> object:
        if args[:2] == ["checkout", "feature/backend/parent"]:
            return MagicMock(returncode=1, stdout="", stderr="local missing")
        if args[:2] == ["fetch", "origin"]:
            return MagicMock(returncode=0, stdout="", stderr="")
        if args[:3] == ["checkout", "-b", "feature/backend/parent"]:
            return MagicMock(returncode=1, stdout="", stderr="origin missing")
        return MagicMock(returncode=0, stdout="", stderr="")

    _bind(svc, "_run_git", AsyncMock(side_effect=_run_git))
    with pytest.raises(GitError, match="Could not check out target branch"):
        await svc._sync_target_branch(
            Path("/tmp/ws"), "feature/backend/parent", "token"
        )


# ---------------------------------------------------------------------------
# _sync_target_branch_best_effort — post-merge local sync must never re-block
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_target_branch_best_effort_swallows_missing_branch() -> None:
    """A post-merge local sync of a target branch gone from origin must NOT raise.

    ``pr_merge`` / ``merge_pull_request`` reach the post-merge sync only after the
    authoritative GitHub merge already succeeded, so updating the local workspace
    copy of the (possibly-deleted) target branch is cosmetic. Letting it raise
    re-blocks the just-merged task and respawn-loops the PM — the live failure
    where an integration branch deleted from origin made ``complete()`` loop on
    ``fetch origin <cell-branch> — fatal: couldn't find remote ref``.
    """
    svc = _service()
    _bind(
        svc,
        "_sync_target_branch",
        AsyncMock(
            side_effect=GitCommandError(
                "fetch origin feature/backend/31ae12fc--0e49e04e",
                "fatal: couldn't find remote ref feature/backend/31ae12fc--0e49e04e",
            )
        ),
    )
    result = await svc._sync_target_branch_best_effort(
        Path("/tmp/ws"), "feature/backend/31ae12fc--0e49e04e", "token"
    )
    assert result is None


@pytest.mark.asyncio
async def test_sync_target_branch_best_effort_returns_sha_on_success() -> None:
    """On success it passes the synced tip sha straight through."""
    svc = _service()
    _bind(svc, "_sync_target_branch", AsyncMock(return_value="merged-tip-sha"))
    result = await svc._sync_target_branch_best_effort(
        Path("/tmp/ws"), "feature/backend/parent", "token"
    )
    assert result == "merged-tip-sha"


# ---------------------------------------------------------------------------
# rebase_onto_base — clean-tree gate (mirrors pull)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rebase_onto_base_refuses_dirty_worktree() -> None:
    """Dirty worktree → ValidationError(DIRTY_WORKSPACE); tree untouched."""
    svc = _service()
    calls: list[list[str]] = []

    async def _run_git(_ws: object, args: list[str], **_kw: object) -> MagicMock:
        calls.append(args)
        res = MagicMock()
        res.returncode = 0
        res.stdout = " M dirty.py\n" if args[:2] == ["status", "--porcelain"] else ""
        return res

    _bind(svc, "_run_git", AsyncMock(side_effect=_run_git))

    with pytest.raises(ValidationError, match="DIRTY_WORKSPACE"):
        await svc.rebase_onto_base(
            Path("/tmp/ws"),
            head_branch="feature/backend/h",
            base_branch="master",
            git_token="t",
        )

    # Only the status probe ran — no fetch/checkout/reset/rebase touched the tree.
    assert calls == [["status", "--porcelain"]]


@pytest.mark.asyncio
async def test_rebase_onto_base_proceeds_on_clean_tree() -> None:
    """Clean worktree → rebase runs; superseded when head has no unique commits."""
    svc = _service()
    calls: list[list[str]] = []

    async def _run_git(_ws: object, args: list[str], **_kw: object) -> MagicMock:
        calls.append(args)
        res = MagicMock()
        res.returncode = 0
        if args[:2] == ["status", "--porcelain"]:
            res.stdout = ""
        elif args[:2] == ["rev-list", "--count"]:
            res.stdout = "0"
        else:
            res.stdout = ""
        return res

    _bind(svc, "_run_git", AsyncMock(side_effect=_run_git))

    result = await svc.rebase_onto_base(
        Path("/tmp/ws"),
        head_branch="feature/backend/h",
        base_branch="master",
        git_token="t",
    )

    assert result == {"status": "superseded"}
    # Gate ran first, then the normal fetch/checkout/reset/rebase sequence.
    assert calls[0] == ["status", "--porcelain"]
    assert ["fetch", "origin"] in calls
    assert ["rebase", "origin/master"] in calls


# ---------------------------------------------------------------------------
# rebase_onto_base — stash=True auto-stash/pop (the dirty-workspace exit)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rebase_onto_base_stash_true_auto_stashes_and_pops() -> None:
    """stash=True: a dirty tree is stashed (not refused), rebased, popped back."""
    svc = _service()
    calls: list[list[str]] = []

    async def _run_git(_ws: object, args: list[str], **_kw: object) -> MagicMock:
        calls.append(args)
        res = MagicMock()
        res.returncode = 0
        if args[:2] == ["status", "--porcelain"]:
            res.stdout = " M dirty.py\n"
        elif args[:2] == ["rev-list", "--count"]:
            res.stdout = "1"
        else:
            res.stdout = ""
        return res

    _bind(svc, "_run_git", AsyncMock(side_effect=_run_git))

    result = await svc.rebase_onto_base(
        Path("/tmp/ws"),
        head_branch="feature/backend/h",
        base_branch="master",
        git_token="t",
        stash=True,
    )

    assert result == {"status": "rebased", "unique_commits": 1}
    push_args = ["stash", "push", "-u", "-m", "sync_branch autostash"]
    pop_args = ["stash", "pop"]
    assert push_args in calls
    assert pop_args in calls
    # Stash push runs before the rebase, pop runs after.
    assert calls.index(push_args) < calls.index(["rebase", "origin/master"])
    assert calls.index(pop_args) > calls.index(["rebase", "origin/master"])


@pytest.mark.asyncio
async def test_rebase_onto_base_stash_pop_conflict_preserves_stash() -> None:
    """A conflicted pop is flagged, never auto-resolved — stash stays intact."""
    svc = _service()

    async def _run_git(_ws: object, args: list[str], **_kw: object) -> MagicMock:
        res = MagicMock()
        res.returncode = 0
        if args[:2] == ["status", "--porcelain"]:
            res.stdout = " M dirty.py\n"
        elif args[:2] == ["rev-list", "--count"]:
            res.stdout = "1"
        elif args == ["stash", "pop"]:
            res.returncode = 1  # pop conflicted — stash is NOT dropped by git
        else:
            res.stdout = ""
        return res

    _bind(svc, "_run_git", AsyncMock(side_effect=_run_git))

    result = await svc.rebase_onto_base(
        Path("/tmp/ws"),
        head_branch="feature/backend/h",
        base_branch="master",
        git_token="t",
        stash=True,
    )

    assert result == {
        "status": "rebased",
        "unique_commits": 1,
        "stash_pop_conflict": True,
    }


@pytest.mark.asyncio
async def test_rebase_onto_base_stash_true_rebase_conflict_skips_pop() -> None:
    """A rebase conflict aborts before ever attempting the pop — no double
    conflict; the stash is reported preserved for the caller to surface."""
    svc = _service()
    calls: list[list[str]] = []

    async def _run_git(_ws: object, args: list[str], **_kw: object) -> MagicMock:
        calls.append(args)
        res = MagicMock()
        res.returncode = 0
        if args[:2] == ["status", "--porcelain"]:
            res.stdout = " M dirty.py\n"
        elif args == ["rebase", "origin/master"]:
            res.returncode = 1
        elif args[:2] == ["diff", "--name-only"]:
            res.stdout = "src/a.py\n"
        else:
            res.stdout = ""
        return res

    _bind(svc, "_run_git", AsyncMock(side_effect=_run_git))

    result = await svc.rebase_onto_base(
        Path("/tmp/ws"),
        head_branch="feature/backend/h",
        base_branch="master",
        git_token="t",
        stash=True,
    )

    assert result == {
        "status": "conflicts",
        "files": ["src/a.py"],
        "stash_preserved": True,
    }
    assert ["stash", "pop"] not in calls


# ---------------------------------------------------------------------------
# _link_commit_to_task — flush; the runner commits (no out-of-band commit)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_link_commit_to_task_does_not_commit_session() -> None:
    """_link_commit_to_task must flush but not commit out-of-band.

    The verb runner owns the transaction boundary; an out-of-band commit
    here would release the runner's savepoint and drag in pending
    orchestrator state.
    """
    session = _make_session()
    svc = GitService(session)

    fake_task_service = MagicMock()
    fake_task = MagicMock(work_session_id=uuid4())
    fake_task_service.get = AsyncMock(return_value=fake_task)
    fake_task_service.add_commit = AsyncMock()

    fake_ws_service = MagicMock()
    fake_ws_service.add_commit = AsyncMock()

    with (
        patch("roboco.services.git.get_task_service", return_value=fake_task_service),
        patch(
            "roboco.services.git.get_work_session_service",
            return_value=fake_ws_service,
        ),
    ):
        await svc._link_commit_to_task(uuid4(), "deadbeef", "msg", uuid4())

    session.flush.assert_awaited()
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_link_commit_to_task_swallows_errors_without_commit() -> None:
    """Even on failure the session is not committed by the link path."""
    session = _make_session()
    svc = GitService(session)

    fake_task_service = MagicMock()
    fake_task_service.get = AsyncMock(side_effect=RuntimeError("boom"))

    with patch("roboco.services.git.get_task_service", return_value=fake_task_service):
        await svc._link_commit_to_task(uuid4(), "deadbeef", "msg", uuid4())

    session.commit.assert_not_awaited()


# ---------------------------------------------------------------------------
# M38: _pr_is_merged returns None on httpx.HTTPError (indeterminate, not
# False). Callers treat None as "assume merged" and fall through to the
# already-merged cleanup path instead of raising MergeConflictError / GitError
# and respawning the PM against an already-merged PR.
# ---------------------------------------------------------------------------


def _httpx_raising_client() -> MagicMock:
    """An AsyncClient whose GET raises httpx.HTTPError (network indeterminate)."""
    fake_client = MagicMock()
    fake_client.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client.__aexit__ = AsyncMock(return_value=False)
    fake_client.get = AsyncMock(side_effect=httpx.HTTPError("network indeterminate"))
    return fake_client


@pytest.mark.asyncio
async def test_pr_is_merged_returns_none_on_httpx_error() -> None:
    """On httpx.HTTPError the lookup is indeterminate -> None, not False."""
    svc = _service()
    with patch(
        "roboco.services.git.httpx.AsyncClient",
        return_value=_httpx_raising_client(),
    ):
        out = await svc._pr_is_merged("acme", "repo", 11, "tok")
    assert out is None


@pytest.mark.asyncio
async def test_merge_with_retry_none_does_not_raise_merge_conflict() -> None:
    """Agent merge path: indeterminate (None) falls through, not conflict."""
    svc = _service()
    _bind(svc, "_first_allowed_merge_method", AsyncMock(return_value=None))
    # 405 -> disambiguation path -> _pr_is_merged returns None -> fall through.
    resp_405 = MagicMock(is_success=False, status_code=405, text="not allowed")
    _bind(svc, "_call_merge_api", AsyncMock(return_value=resp_405))
    _bind(svc, "_pr_is_merged", AsyncMock(return_value=None))

    ctx = GitService._MergeContext(
        owner="acme",
        repo="repo",
        pr_number=11,
        git_token="tok",
        workspace=Path("/tmp/ws"),
        target="feature/main_pm/root1",
    )
    # None must NOT raise MergeConflictError; it returns the failed resp
    # (idempotent-success fall-through, same as the already-merged True path).
    out = await svc._merge_with_retry(ctx)
    assert out is resp_405


@pytest.mark.asyncio
async def test_merge_with_retry_false_raises_merge_conflict() -> None:
    """A real False (PR not merged) still raises MergeConflictError."""
    svc = _service()
    _bind(svc, "_first_allowed_merge_method", AsyncMock(return_value=None))
    resp_405 = MagicMock(is_success=False, status_code=405, text="not mergeable")
    _bind(svc, "_call_merge_api", AsyncMock(return_value=resp_405))
    _bind(svc, "_pr_is_merged", AsyncMock(return_value=False))

    ctx = GitService._MergeContext(
        owner="acme",
        repo="repo",
        pr_number=11,
        git_token="tok",
        workspace=Path("/tmp/ws"),
        target="feature/main_pm/root1",
    )
    with pytest.raises(MergeConflictError):
        await svc._merge_with_retry(ctx)


@pytest.mark.asyncio
async def test_merge_pull_request_none_does_not_raise_git_error() -> None:
    """CEO merge path: indeterminate (None) falls through to cleanup, not GitError."""
    svc = _service()
    _bind(svc, "_get_project_token_or_raise", AsyncMock(return_value="tok"))
    _bind(svc, "_parse_github_remote", MagicMock(return_value=("acme", "repo")))
    _bind(svc, "_first_allowed_merge_method", AsyncMock(return_value=None))
    _bind(svc, "_delete_pr_branch_best_effort", AsyncMock())
    _bind(svc, "_sync_target_branch", AsyncMock(return_value="abc123sha"))
    _bind(svc, "_project_default_branch", AsyncMock(return_value="master"))
    resp_405 = MagicMock(is_success=False, status_code=405, text="not allowed")
    _bind(svc, "_call_merge_api", AsyncMock(return_value=resp_405))
    _bind(svc, "_pr_is_merged", AsyncMock(return_value=None))

    out = await svc.merge_pull_request(Path("/tmp/ws"), 11, "squash", "roboco")
    assert out == ("master", "abc123sha")


@pytest.mark.asyncio
async def test_is_pr_merged_for_task_none_treated_as_merged() -> None:
    """is_pr_merged_for_task coerces None -> True so choreographer skips pr_merge."""
    project_id = uuid4()
    fake_task = MagicMock(project_id=project_id, pr_number=11, parent_task_id=None)
    fake_project = MagicMock(slug="roboco")
    result = MagicMock()
    result.scalar_one_or_none.return_value = fake_task

    svc = _service(execute_returns=result)
    _bind(svc, "get_workspace", AsyncMock(return_value=Path("/tmp/ws")))
    _bind(svc, "_get_project_token_or_raise", AsyncMock(return_value="tok"))
    _bind(svc, "_parse_github_remote", MagicMock(return_value=("acme", "repo")))
    _bind(svc, "_project_for_task", AsyncMock(return_value=fake_project))
    _bind(svc, "_resolve_workspace_agent_id", MagicMock(return_value=None))
    _bind(svc, "_pr_is_merged", AsyncMock(return_value=None))

    with _patch_project_service(fake_project):
        out = await svc.is_pr_merged_for_task(fake_task.id)
    assert out is True


# ---------------------------------------------------------------------------
# L1: actor_agent_id threading + narrowed created_by fallback
# ---------------------------------------------------------------------------


def test_resolve_workspace_agent_id_no_created_by_fallback_when_actor_none() -> None:
    """_resolve_workspace_agent_id(None) must NOT fall back to created_by.

    A PM who created the task but never cloned the project's workspace
    would 404 on get_workspace(PM-id). With the actor unset we prefer
    None (project.workspace_path) over a creator who has no clone.
    """
    pm_id = uuid4()
    task = MagicMock(assigned_to=None, created_by=pm_id)
    assert GitService._resolve_workspace_agent_id(task, None) is None


def test_resolve_workspace_agent_id_actor_precedes_assigned_and_creator() -> None:
    """The threaded actor wins over assigned_to and created_by."""
    actor = uuid4()
    assignee = uuid4()
    creator = uuid4()
    task = MagicMock(assigned_to=assignee, created_by=creator)
    assert GitService._resolve_workspace_agent_id(task, actor) == actor


def test_resolve_workspace_agent_id_assigned_to_used_when_actor_none() -> None:
    """assigned_to is still the fallback when the actor is unset."""
    assignee = uuid4()
    creator = uuid4()
    task = MagicMock(assigned_to=assignee, created_by=creator)
    assert GitService._resolve_workspace_agent_id(task, None) == assignee


@pytest.mark.asyncio
async def test_update_pr_for_task_threads_actor_agent_id() -> None:
    """update_pr_for_task forwards actor_agent_id to workspace resolution.

    A PM (not the assignee) editing the PR must resolve to the PM's own
    workspace, not fall back to assigned_to/created_by. The actor is the
    agent who actually performed the action — the verb layer passes it.
    """
    pm_id = uuid4()
    task = MagicMock(
        id=uuid4(),
        project_id=uuid4(),
        pr_number=7,
        pr_url="https://github.com/acme/repo/pull/7",
        assigned_to=uuid4(),
        created_by=uuid4(),
    )

    svc = _service()
    captured: dict[str, object] = {}

    async def _capture_workspace(_slug: str, agent_id: UUID | None = None) -> Path:
        captured["agent_id"] = agent_id
        return Path("/tmp/ws")

    _bind(svc, "get_workspace", AsyncMock(side_effect=_capture_workspace))
    _bind(svc, "_parse_github_remote", MagicMock(return_value=("acme", "repo")))
    _bind(svc, "_get_project_token_or_raise", AsyncMock(return_value="tok"))

    fake_task_service = MagicMock()
    fake_task_service.get = AsyncMock(return_value=task)
    fake_project = MagicMock(slug="roboco")

    patch_resp = MagicMock()
    patch_resp.status_code = 200
    patch_resp.is_success = True
    patch_resp.json.return_value = {"number": 7, "html_url": task.pr_url}
    patch_resp.text = ""
    fake_client = MagicMock()
    fake_client.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client.__aexit__ = AsyncMock(return_value=False)
    fake_client.patch = AsyncMock(return_value=patch_resp)
    fake_client.post = AsyncMock()

    with (
        patch("roboco.services.git.get_task_service", return_value=fake_task_service),
        _patch_project_service(fake_project),
        patch("roboco.services.git.httpx.AsyncClient", return_value=fake_client),
    ):
        await svc.update_pr_for_task(
            UUID(str(task.id)),
            title="t",
            body=None,
            reviewers=None,
            actor_agent_id=pm_id,
        )

    assert captured["agent_id"] == pm_id
