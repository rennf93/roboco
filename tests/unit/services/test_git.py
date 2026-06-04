"""Unit tests for GitService gateway-backfill methods.

These tests target signature-level behavior. Full integration with the
GitHub REST API + filesystem lives under integration tests; here we
mock the network and filesystem boundaries.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from roboco.config import settings
from roboco.services.base import NotFoundError
from roboco.services.git import GitService

if TYPE_CHECKING:
    from contextlib import AbstractContextManager

_EXPECTED_PR_NUMBER = 7


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
        out = await svc.pr_target(42)
    assert out == "feature/parent"


@pytest.mark.asyncio
async def test_pr_target_raises_when_pr_not_found() -> None:
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    svc = _service(execute_returns=result)
    with pytest.raises(NotFoundError):
        await svc.pr_target(99)


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

    fake_resp = MagicMock()
    fake_resp.is_success = True
    fake_resp.status_code = 201
    fake_resp.json.return_value = {
        "number": _EXPECTED_PR_NUMBER,
        "html_url": f"https://github.com/acme/repo/pull/{_EXPECTED_PR_NUMBER}",
    }
    _bind(svc, "_post_pr", AsyncMock(return_value=fake_resp))

    with _patch_project_service(fake_project):
        out = await svc.create_pr(
            "feature/backend/abc12345", parent="master", is_root_pr=True
        )
    assert out["pr_number"] == _EXPECTED_PR_NUMBER
    assert "github.com" in out["pr_url"]
    assert out["is_root_pr"] is True


@pytest.mark.asyncio
async def test_create_pr_raises_when_branch_not_found() -> None:
    svc = _service()
    _bind(svc, "_task_for_branch", AsyncMock(return_value=None))
    with pytest.raises(NotFoundError):
        await svc.create_pr("missing/branch", parent="master", is_root_pr=False)


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

    with _patch_project_service(fake_project):
        out = await svc.pr_merge(11, target="master")
    assert out == {"merge_commit_sha": "abc123sha"}


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
async def test_create_branch_idempotent_when_branch_already_exists() -> None:
    # A prior attempt may have created the branch on disk before the DB recorded
    # branch_name; `checkout -b` then fails 128. create_branch must switch to the
    # existing branch instead of raising (the raise triggered a retry cascade).
    from roboco.api.schemas.git import GitCreateBranchRequest

    branch = "feature/backend/abc12345--def67890"
    svc = _service()
    object.__setattr__(svc, "_resolve_base_branch", AsyncMock(return_value="master"))
    object.__setattr__(svc, "_project_default_branch", AsyncMock(return_value="master"))
    object.__setattr__(svc, "_token_for_project", AsyncMock(return_value=None))
    object.__setattr__(
        svc, "_checkout_base_with_fallback", AsyncMock(return_value="master")
    )

    calls: list[list[str]] = []

    async def fake_run_git(_workspace: object, args: list[str], **_kw: object) -> object:
        calls.append(list(args))
        rc = 1 if list(args[:2]) == ["checkout", "-b"] else 0
        return MagicMock(stdout="", returncode=rc)

    object.__setattr__(svc, "_run_git", fake_run_git)

    with (
        patch("roboco.services.git.build_branch_name", AsyncMock(return_value=branch)),
        patch(
            "roboco.services.git.get_task_service",
            MagicMock(return_value=MagicMock(update=AsyncMock())),
        ),
    ):
        await svc.create_branch(
            Path("/tmp/ws"),
            "backend",
            GitCreateBranchRequest(
                project_slug="roboco-api",
                task_id=uuid4(),
                branch_type="feature",
                agent_id=str(uuid4()),
                parent_branch=None,
            ),
        )

    assert ["checkout", "-b", branch] in calls, "checkout -b attempted"
    assert ["checkout", branch] in calls, "fell back to existing branch on 128"
