"""Git API route coverage."""

from __future__ import annotations

import uuid
from http import HTTPStatus
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from roboco.api.deps import get_agent_context, get_db
from roboco.api.routes.git import _translate_error
from roboco.api.routes.git import router as git_router
from roboco.db.tables import AgentTable, ProjectTable
from roboco.exceptions import GitCommandError, GitTimeoutError
from roboco.models import AgentRole, AgentStatus, Team
from roboco.models.permissions import AgentContext
from roboco.services.base import (
    NotFoundError,
    ServiceError,
    UnauthorizedError,
    ValidationError,
)

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession


@pytest_asyncio.fixture
async def git_client(
    db_session: AsyncSession,
) -> AsyncIterator[dict]:
    agent = AgentTable(
        id=uuid4(),
        name="Dev",
        slug=f"be-dev-{uuid4().hex[:8]}",
        role=AgentRole.DEVELOPER,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="dev",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add(agent)
    await db_session.flush()
    project = ProjectTable(
        id=uuid4(),
        name="GitProj",
        slug=f"git-proj-{uuid4().hex[:6]}",
        git_url="https://example.com/r.git",
        assigned_cell=Team.BACKEND,
        created_by=agent.id,
    )
    db_session.add(project)
    await db_session.flush()

    app = FastAPI()
    app.include_router(git_router, prefix="/api/git")

    async def _override_db() -> AsyncGenerator[AsyncSession]:
        yield db_session

    async def _override_agent() -> AgentContext:
        return AgentContext(
            agent_id=cast("uuid.UUID", agent.id),
            role=AgentRole.DEVELOPER,
            team=Team.BACKEND,
        )

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_agent_context] = _override_agent

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield {"client": client, "agent": agent, "project": project, "db": db_session}
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def pm_git_client(
    db_session: AsyncSession,
) -> AsyncIterator[dict]:
    """Like git_client but with CELL_PM role — required for the rebase endpoint."""
    agent = AgentTable(
        id=uuid4(),
        name="PM",
        slug=f"be-pm-{uuid4().hex[:8]}",
        role=AgentRole.CELL_PM,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="pm",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add(agent)
    await db_session.flush()
    project = ProjectTable(
        id=uuid4(),
        name="GitProj",
        slug=f"git-proj-{uuid4().hex[:6]}",
        git_url="https://example.com/r.git",
        assigned_cell=Team.BACKEND,
        created_by=agent.id,
    )
    db_session.add(project)
    await db_session.flush()

    app = FastAPI()
    app.include_router(git_router, prefix="/api/git")

    async def _override_db() -> AsyncGenerator[AsyncSession]:
        yield db_session

    async def _override_agent() -> AgentContext:
        return AgentContext(
            agent_id=cast("uuid.UUID", agent.id),
            role=AgentRole.CELL_PM,
            team=Team.BACKEND,
        )

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_agent_context] = _override_agent

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield {"client": client, "agent": agent, "project": project, "db": db_session}
    app.dependency_overrides.clear()


_HDR = {"X-Agent-ID": str(uuid4()), "X-Agent-Role": "developer"}


# ---------------------------------------------------------------------------
# project resolution
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_status_project_not_found(git_client: dict) -> None:
    response = await git_client["client"].get(
        "/api/git/status?project_slug=ghost-project", headers=_HDR
    )
    assert response.status_code == HTTPStatus.NOT_FOUND


# ---------------------------------------------------------------------------
# get_git_status
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_status_success(git_client: dict) -> None:
    workspace = "/tmp/ws"
    with patch("roboco.api.routes.git.get_git_service") as mock_get:
        svc = AsyncMock()
        svc.get_workspace = AsyncMock(return_value=workspace)
        svc.get_status = AsyncMock(return_value=("main", False, [], [], [], 0, 0))
        mock_get.return_value = svc
        response = await git_client["client"].get(
            f"/api/git/status?project_slug={git_client['project'].slug}",
            headers=_HDR,
        )
    assert response.status_code == HTTPStatus.OK


@pytest.mark.asyncio
async def test_status_validation_error(git_client: dict) -> None:
    with patch("roboco.api.routes.git.get_git_service") as mock_get:
        svc = AsyncMock()
        svc.get_workspace = AsyncMock(side_effect=ValidationError("bad"))
        mock_get.return_value = svc
        response = await git_client["client"].get(
            f"/api/git/status?project_slug={git_client['project'].slug}",
            headers=_HDR,
        )
    assert response.status_code == HTTPStatus.BAD_REQUEST


@pytest.mark.asyncio
async def test_status_unauthorized(git_client: dict) -> None:
    with patch("roboco.api.routes.git.get_git_service") as mock_get:
        svc = AsyncMock()
        svc.get_workspace = AsyncMock(side_effect=UnauthorizedError("nope"))
        mock_get.return_value = svc
        response = await git_client["client"].get(
            f"/api/git/status?project_slug={git_client['project'].slug}",
            headers=_HDR,
        )
    assert response.status_code == HTTPStatus.FORBIDDEN


@pytest.mark.asyncio
async def test_status_not_found(git_client: dict) -> None:
    with patch("roboco.api.routes.git.get_git_service") as mock_get:
        svc = AsyncMock()
        svc.get_workspace = AsyncMock(side_effect=NotFoundError("missing"))
        mock_get.return_value = svc
        response = await git_client["client"].get(
            f"/api/git/status?project_slug={git_client['project'].slug}",
            headers=_HDR,
        )
    assert response.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.asyncio
async def test_status_git_timeout_directly() -> None:
    """Exercise _translate_error's GitTimeoutError branch directly.

    The route uses `except ServiceError as e` from services.base, but
    GitTimeoutError extends roboco.exceptions.ServiceError (different
    class), so it never enters _translate_error in practice. We invoke
    the helper directly to cover the branch.
    """
    err = GitTimeoutError("git status", 10)
    http_exc = _translate_error(err)
    assert http_exc.status_code == HTTPStatus.GATEWAY_TIMEOUT


@pytest.mark.asyncio
async def test_status_git_command_error_directly() -> None:
    """Direct invocation of _translate_error's GitCommandError branch."""
    err = GitCommandError("git status", "stderr")
    http_exc = _translate_error(err)
    assert http_exc.status_code == HTTPStatus.INTERNAL_SERVER_ERROR


# ---------------------------------------------------------------------------
# log
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_log_with_branch_success(git_client: dict) -> None:
    # Fields are \x1f-delimited (not "|"): the second commit's subject contains
    # a literal "|" (e.g. "curl|sh") which used to shift the split and land the
    # author+date in one field, 500-ing on datetime.fromisoformat.
    log_result = MagicMock()
    log_result.returncode = 0
    log_result.stdout = (
        "abc123\x1fabc\x1ffix bug\x1fme\x1f2026-01-01T00:00:00Z\n"
        "def456\x1fdef\x1fclose curl|sh RCE\x1fyou\x1f2026-01-01T00:00:01Z\n"
    )
    with patch("roboco.api.routes.git.get_git_service") as mock_get:
        svc = AsyncMock()
        svc.get_workspace = AsyncMock(return_value="/tmp/ws")
        svc._run_git = AsyncMock(return_value=log_result)
        mock_get.return_value = svc
        response = await git_client["client"].get(
            f"/api/git/log?project_slug={git_client['project'].slug}&branch=feature/x",
            headers=_HDR,
        )
    assert response.status_code == HTTPStatus.OK
    commits = response.json()["commits"]
    assert [c["message"] for c in commits] == ["fix bug", "close curl|sh RCE"]
    assert [c["author"] for c in commits] == ["me", "you"]


@pytest.mark.asyncio
async def test_log_no_branch_fetches_current(git_client: dict) -> None:
    log_result = MagicMock()
    log_result.returncode = 0
    log_result.stdout = ""
    with patch("roboco.api.routes.git.get_git_service") as mock_get:
        svc = AsyncMock()
        svc.get_workspace = AsyncMock(return_value="/tmp/ws")
        svc.get_current_branch = AsyncMock(return_value="main")
        svc._run_git = AsyncMock(return_value=log_result)
        mock_get.return_value = svc
        response = await git_client["client"].get(
            f"/api/git/log?project_slug={git_client['project'].slug}",
            headers=_HDR,
        )
    assert response.status_code == HTTPStatus.OK


@pytest.mark.asyncio
async def test_log_unknown_branch_returns_empty(git_client: dict) -> None:
    log_result = MagicMock()
    log_result.returncode = 1
    log_result.stderr = "no such branch"
    log_result.stdout = ""
    with patch("roboco.api.routes.git.get_git_service") as mock_get:
        svc = AsyncMock()
        svc.get_workspace = AsyncMock(return_value="/tmp/ws")
        svc._run_git = AsyncMock(return_value=log_result)
        mock_get.return_value = svc
        response = await git_client["client"].get(
            f"/api/git/log?project_slug={git_client['project'].slug}&branch=ghost",
            headers=_HDR,
        )
    assert response.status_code == HTTPStatus.OK
    assert response.json()["commits"] == []


@pytest.mark.asyncio
async def test_log_service_error(git_client: dict) -> None:
    with patch("roboco.api.routes.git.get_git_service") as mock_get:
        svc = AsyncMock()
        svc.get_workspace = AsyncMock(side_effect=NotFoundError("no ws"))
        mock_get.return_value = svc
        response = await git_client["client"].get(
            f"/api/git/log?project_slug={git_client['project'].slug}",
            headers=_HDR,
        )
    assert response.status_code == HTTPStatus.NOT_FOUND


# ---------------------------------------------------------------------------
# branches
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_branches_local_only(git_client: dict) -> None:
    branch_result = MagicMock()
    branch_result.stdout = "main|abc123\nfeature/x|def456\n"
    with patch("roboco.api.routes.git.get_git_service") as mock_get:
        svc = AsyncMock()
        svc.get_workspace = AsyncMock(return_value="/tmp/ws")
        svc.get_current_branch = AsyncMock(return_value="main")
        svc._run_git = AsyncMock(return_value=branch_result)
        mock_get.return_value = svc
        response = await git_client["client"].get(
            f"/api/git/branches?project_slug={git_client['project'].slug}",
            headers=_HDR,
        )
    assert response.status_code == HTTPStatus.OK


@pytest.mark.asyncio
async def test_branches_with_remote(git_client: dict) -> None:
    branch_result = MagicMock()
    branch_result.stdout = "main|abc123\nremotes/origin/feature/y|def456\n"
    with patch("roboco.api.routes.git.get_git_service") as mock_get:
        svc = AsyncMock()
        svc.get_workspace = AsyncMock(return_value="/tmp/ws")
        svc.get_current_branch = AsyncMock(return_value="main")
        svc._run_git = AsyncMock(return_value=branch_result)
        mock_get.return_value = svc
        response = await git_client["client"].get(
            f"/api/git/branches?project_slug={git_client['project'].slug}"
            "&include_remote=true",
            headers=_HDR,
        )
    assert response.status_code == HTTPStatus.OK


@pytest.mark.asyncio
async def test_branches_skips_empty_lines(git_client: dict) -> None:
    """Line 246: empty line in branch output triggers continue."""
    branch_result = MagicMock()
    # Embed an empty line between two branches.
    branch_result.stdout = "main|abc\n\nfeature/x|def\n"
    with patch("roboco.api.routes.git.get_git_service") as mock_get:
        svc = AsyncMock()
        svc.get_workspace = AsyncMock(return_value="/tmp/ws")
        svc.get_current_branch = AsyncMock(return_value="main")
        svc._run_git = AsyncMock(return_value=branch_result)
        mock_get.return_value = svc
        response = await git_client["client"].get(
            f"/api/git/branches?project_slug={git_client['project'].slug}",
            headers=_HDR,
        )
    assert response.status_code == HTTPStatus.OK


@pytest.mark.asyncio
async def test_branches_service_error(git_client: dict) -> None:
    with patch("roboco.api.routes.git.get_git_service") as mock_get:
        svc = AsyncMock()
        svc.get_workspace = AsyncMock(side_effect=NotFoundError("no ws"))
        mock_get.return_value = svc
        response = await git_client["client"].get(
            f"/api/git/branches?project_slug={git_client['project'].slug}",
            headers=_HDR,
        )
    assert response.status_code == HTTPStatus.NOT_FOUND


# ---------------------------------------------------------------------------
# diff
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_diff_basic(git_client: dict) -> None:
    diff_res = MagicMock(stdout="some diff")
    stat_res = MagicMock(stdout="a.py | 2\nb.py | 4\n2 files changed\n")
    with patch("roboco.api.routes.git.get_git_service") as mock_get:
        svc = AsyncMock()
        svc.get_workspace = AsyncMock(return_value="/tmp/ws")
        svc._run_git = AsyncMock(side_effect=[diff_res, stat_res])
        mock_get.return_value = svc
        response = await git_client["client"].get(
            f"/api/git/diff?project_slug={git_client['project'].slug}",
            headers=_HDR,
        )
    assert response.status_code == HTTPStatus.OK


@pytest.mark.asyncio
async def test_diff_staged_with_file(git_client: dict) -> None:
    diff_res = MagicMock(stdout="")
    stat_res = MagicMock(stdout="")
    with patch("roboco.api.routes.git.get_git_service") as mock_get:
        svc = AsyncMock()
        svc.get_workspace = AsyncMock(return_value="/tmp/ws")
        svc._run_git = AsyncMock(side_effect=[diff_res, stat_res])
        mock_get.return_value = svc
        response = await git_client["client"].get(
            f"/api/git/diff?project_slug={git_client['project'].slug}"
            "&staged=true&file_path=a.py",
            headers=_HDR,
        )
    assert response.status_code == HTTPStatus.OK


@pytest.mark.asyncio
async def test_diff_service_error(git_client: dict) -> None:
    with patch("roboco.api.routes.git.get_git_service") as mock_get:
        svc = AsyncMock()
        svc.get_workspace = AsyncMock(side_effect=NotFoundError("no ws"))
        mock_get.return_value = svc
        response = await git_client["client"].get(
            f"/api/git/diff?project_slug={git_client['project'].slug}",
            headers=_HDR,
        )
    assert response.status_code == HTTPStatus.NOT_FOUND


# ---------------------------------------------------------------------------
# commit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_commit_success(git_client: dict) -> None:
    with patch("roboco.api.routes.git.get_git_service") as mock_get:
        svc = AsyncMock()
        svc.commit_for_task = AsyncMock(return_value=("abc123", "msg", 1, 5, 2))
        mock_get.return_value = svc
        response = await git_client["client"].post(
            "/api/git/commit",
            json={
                "project_slug": git_client["project"].slug,
                "task_id": str(uuid4()),
                "agent_id": str(uuid4()),
                "message": "fix some thing",
                "commit_type": "fix",
            },
            headers=_HDR,
        )
    assert response.status_code == HTTPStatus.OK


@pytest.mark.asyncio
async def test_commit_service_error(git_client: dict) -> None:
    with patch("roboco.api.routes.git.get_git_service") as mock_get:
        svc = AsyncMock()
        svc.commit_for_task = AsyncMock(side_effect=ValidationError("bad"))
        mock_get.return_value = svc
        response = await git_client["client"].post(
            "/api/git/commit",
            json={
                "project_slug": git_client["project"].slug,
                "task_id": str(uuid4()),
                "agent_id": str(uuid4()),
                "message": "fix some thing",
                "commit_type": "fix",
            },
            headers=_HDR,
        )
    assert response.status_code == HTTPStatus.BAD_REQUEST


# ---------------------------------------------------------------------------
# push
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_push_success(git_client: dict) -> None:
    with patch("roboco.api.routes.git.get_git_service") as mock_get:
        svc = AsyncMock()
        svc.push_for_task = AsyncMock(return_value=("feature/x", 2))
        mock_get.return_value = svc
        response = await git_client["client"].post(
            "/api/git/push",
            json={
                "project_slug": git_client["project"].slug,
                "task_id": str(uuid4()),
                "agent_id": str(uuid4()),
            },
            headers=_HDR,
        )
    assert response.status_code == HTTPStatus.OK


@pytest.mark.asyncio
async def test_push_service_error(git_client: dict) -> None:
    with patch("roboco.api.routes.git.get_git_service") as mock_get:
        svc = AsyncMock()
        svc.push_for_task = AsyncMock(side_effect=NotFoundError("missing"))
        mock_get.return_value = svc
        response = await git_client["client"].post(
            "/api/git/push",
            json={
                "project_slug": git_client["project"].slug,
                "task_id": str(uuid4()),
                "agent_id": str(uuid4()),
            },
            headers=_HDR,
        )
    assert response.status_code == HTTPStatus.NOT_FOUND


# ---------------------------------------------------------------------------
# branch/create
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_branch_success(git_client: dict) -> None:
    with patch("roboco.api.routes.git.get_git_service") as mock_get:
        svc = AsyncMock()
        svc.create_branch_for_task = AsyncMock(
            return_value=("feature/backend/X", "main")
        )
        mock_get.return_value = svc
        response = await git_client["client"].post(
            "/api/git/branch/create",
            json={
                "project_slug": git_client["project"].slug,
                "task_id": str(uuid4()),
                "branch_type": "feature",
                "agent_id": str(uuid4()),
            },
            headers=_HDR,
        )
    assert response.status_code == HTTPStatus.OK


@pytest.mark.asyncio
async def test_create_branch_service_error(git_client: dict) -> None:
    with patch("roboco.api.routes.git.get_git_service") as mock_get:
        svc = AsyncMock()
        svc.create_branch_for_task = AsyncMock(side_effect=UnauthorizedError("no perm"))
        mock_get.return_value = svc
        response = await git_client["client"].post(
            "/api/git/branch/create",
            json={
                "project_slug": git_client["project"].slug,
                "task_id": str(uuid4()),
                "branch_type": "feature",
                "agent_id": str(uuid4()),
            },
            headers=_HDR,
        )
    assert response.status_code == HTTPStatus.FORBIDDEN


# ---------------------------------------------------------------------------
# checkout
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_checkout_success(git_client: dict) -> None:
    with patch("roboco.api.routes.git.get_git_service") as mock_get:
        svc = AsyncMock()
        svc.checkout_branch_for_agent = AsyncMock(return_value=None)
        mock_get.return_value = svc
        response = await git_client["client"].post(
            "/api/git/checkout",
            json={
                "project_slug": git_client["project"].slug,
                "branch": "feature/x",
                "agent_id": str(uuid4()),
            },
            headers=_HDR,
        )
    assert response.status_code == HTTPStatus.OK


@pytest.mark.asyncio
async def test_checkout_service_error(git_client: dict) -> None:
    with patch("roboco.api.routes.git.get_git_service") as mock_get:
        svc = AsyncMock()
        svc.checkout_branch_for_agent = AsyncMock(side_effect=UnauthorizedError("nope"))
        mock_get.return_value = svc
        response = await git_client["client"].post(
            "/api/git/checkout",
            json={
                "project_slug": git_client["project"].slug,
                "branch": "master",
                "agent_id": str(uuid4()),
            },
            headers=_HDR,
        )
    assert response.status_code == HTTPStatus.FORBIDDEN


# ---------------------------------------------------------------------------
# pr/create
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_pr_success(git_client: dict) -> None:
    with patch("roboco.api.routes.git.get_git_service") as mock_get:
        svc = AsyncMock()
        svc.create_pr_for_task = AsyncMock(
            return_value=(42, "https://github.com/x/y/pull/42", "T", "feat", "main")
        )
        mock_get.return_value = svc
        response = await git_client["client"].post(
            "/api/git/pr/create",
            json={
                "project_slug": git_client["project"].slug,
                "task_id": str(uuid4()),
                "agent_id": str(uuid4()),
            },
            headers=_HDR,
        )
    assert response.status_code == HTTPStatus.OK


@pytest.mark.asyncio
async def test_create_pr_service_error(git_client: dict) -> None:
    with patch("roboco.api.routes.git.get_git_service") as mock_get:
        svc = AsyncMock()
        svc.create_pr_for_task = AsyncMock(side_effect=ValidationError("bad"))
        mock_get.return_value = svc
        response = await git_client["client"].post(
            "/api/git/pr/create",
            json={
                "project_slug": git_client["project"].slug,
                "task_id": str(uuid4()),
                "agent_id": str(uuid4()),
            },
            headers=_HDR,
        )
    assert response.status_code == HTTPStatus.BAD_REQUEST


# ---------------------------------------------------------------------------
# pr/merge
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_merge_pr_success(git_client: dict) -> None:
    with patch("roboco.api.routes.git.get_git_service") as mock_get:
        svc = AsyncMock()
        svc.merge_pr_for_task = AsyncMock(return_value=("main", "abc"))
        mock_get.return_value = svc
        response = await git_client["client"].post(
            "/api/git/pr/merge",
            json={
                "project_slug": git_client["project"].slug,
                "pr_number": 42,
                "task_id": str(uuid4()),
                "agent_id": str(uuid4()),
            },
            headers=_HDR,
        )
    assert response.status_code == HTTPStatus.OK


@pytest.mark.asyncio
async def test_merge_pr_service_error(git_client: dict) -> None:
    with patch("roboco.api.routes.git.get_git_service") as mock_get:
        svc = AsyncMock()
        svc.merge_pr_for_task = AsyncMock(side_effect=NotFoundError("no PR"))
        mock_get.return_value = svc
        response = await git_client["client"].post(
            "/api/git/pr/merge",
            json={
                "project_slug": git_client["project"].slug,
                "pr_number": 42,
                "task_id": str(uuid4()),
                "agent_id": str(uuid4()),
            },
            headers=_HDR,
        )
    assert response.status_code == HTTPStatus.NOT_FOUND


# ---------------------------------------------------------------------------
# UUID resolution path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_status_with_uuid(git_client: dict) -> None:
    """Pass a UUID string instead of slug — should look up by UUID."""
    with patch("roboco.api.routes.git.get_git_service") as mock_get:
        svc = AsyncMock()
        svc.get_workspace = AsyncMock(return_value="/tmp/ws")
        svc.get_status = AsyncMock(return_value=("main", False, [], [], [], 0, 0))
        mock_get.return_value = svc
        response = await git_client["client"].get(
            f"/api/git/status?project_slug={git_client['project'].id}",
            headers=_HDR,
        )
    assert response.status_code == HTTPStatus.OK


# ---------------------------------------------------------------------------
# Generic ServiceError -> default 500
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_status_generic_service_error(git_client: dict) -> None:
    """Any other ServiceError becomes 500."""

    class CustomError(ServiceError):
        pass

    with patch("roboco.api.routes.git.get_git_service") as mock_get:
        svc = AsyncMock()
        svc.get_workspace = AsyncMock(side_effect=CustomError("err"))
        mock_get.return_value = svc
        response = await git_client["client"].get(
            f"/api/git/status?project_slug={git_client['project'].slug}",
            headers=_HDR,
        )
    assert response.status_code == HTTPStatus.INTERNAL_SERVER_ERROR


# ---------------------------------------------------------------------------
# pull
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pull_success(git_client: dict) -> None:
    with patch("roboco.api.routes.git.get_git_service") as mock_get:
        svc = AsyncMock()
        svc.get_workspace = AsyncMock(return_value="/tmp/ws")
        svc.pull = AsyncMock(return_value=("main", False, [], [], [], 0, 0))
        mock_get.return_value = svc
        response = await git_client["client"].post(
            "/api/git/pull",
            json={
                "project_slug": git_client["project"].slug,
                "task_id": str(uuid4()),
                "agent_id": str(uuid4()),
            },
            headers=_HDR,
        )
    assert response.status_code == HTTPStatus.OK
    data = response.json()
    assert data["current_branch"] == "main"
    assert "ahead" in data
    assert "behind" in data
    assert "has_changes" in data
    assert "staged_files" in data
    assert "unstaged_files" in data
    assert "untracked_files" in data


@pytest.mark.asyncio
async def test_pull_git_command_error(git_client: dict) -> None:
    with patch("roboco.api.routes.git.get_git_service") as mock_get:
        svc = AsyncMock()
        svc.get_workspace = AsyncMock(return_value="/tmp/ws")
        svc.pull = AsyncMock(side_effect=GitCommandError("pull", "network error"))
        mock_get.return_value = svc
        response = await git_client["client"].post(
            "/api/git/pull",
            json={
                "project_slug": git_client["project"].slug,
                "task_id": str(uuid4()),
                "agent_id": str(uuid4()),
            },
            headers=_HDR,
        )
    assert response.status_code == HTTPStatus.INTERNAL_SERVER_ERROR


# ---------------------------------------------------------------------------
# fetch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_success(git_client: dict) -> None:
    with patch("roboco.api.routes.git.get_git_service") as mock_get:
        svc = AsyncMock()
        svc.get_workspace = AsyncMock(return_value="/tmp/ws")
        _ahead = 2
        _behind = 1
        svc.fetch = AsyncMock(
            return_value=("feature/x", True, ["a.py"], [], [], _ahead, _behind)
        )
        mock_get.return_value = svc
        response = await git_client["client"].post(
            "/api/git/fetch",
            json={
                "project_slug": git_client["project"].slug,
                "task_id": str(uuid4()),
                "agent_id": str(uuid4()),
            },
            headers=_HDR,
        )
    assert response.status_code == HTTPStatus.OK
    data = response.json()
    assert data["current_branch"] == "feature/x"
    assert data["ahead"] == _ahead
    assert data["behind"] == _behind
    assert data["has_changes"] is True
    assert "staged_files" in data
    assert "unstaged_files" in data
    assert "untracked_files" in data


@pytest.mark.asyncio
async def test_fetch_git_command_error(git_client: dict) -> None:
    with patch("roboco.api.routes.git.get_git_service") as mock_get:
        svc = AsyncMock()
        svc.get_workspace = AsyncMock(return_value="/tmp/ws")
        svc.fetch = AsyncMock(side_effect=GitCommandError("fetch", "network error"))
        mock_get.return_value = svc
        response = await git_client["client"].post(
            "/api/git/fetch",
            json={
                "project_slug": git_client["project"].slug,
                "task_id": str(uuid4()),
                "agent_id": str(uuid4()),
            },
            headers=_HDR,
        )
    assert response.status_code == HTTPStatus.INTERNAL_SERVER_ERROR


# ---------------------------------------------------------------------------
# rebase
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rebase_success(pm_git_client: dict) -> None:
    with patch("roboco.api.routes.git.get_git_service") as mock_get:
        svc = AsyncMock()
        svc.get_workspace = AsyncMock(return_value="/tmp/ws")
        svc.rebase = AsyncMock(return_value=(False, []))
        mock_get.return_value = svc
        response = await pm_git_client["client"].post(
            "/api/git/rebase",
            json={
                "project_slug": pm_git_client["project"].slug,
                "target_branch": "develop",
            },
            headers=_HDR,
        )
    assert response.status_code == HTTPStatus.OK
    data = response.json()
    assert data["conflict"] is False
    assert data["conflicted_files"] == []


@pytest.mark.asyncio
async def test_rebase_conflict(pm_git_client: dict) -> None:
    with patch("roboco.api.routes.git.get_git_service") as mock_get:
        svc = AsyncMock()
        svc.get_workspace = AsyncMock(return_value="/tmp/ws")
        svc.rebase = AsyncMock(return_value=(True, ["src/foo.py", "src/bar.py"]))
        mock_get.return_value = svc
        response = await pm_git_client["client"].post(
            "/api/git/rebase",
            json={
                "project_slug": pm_git_client["project"].slug,
                "target_branch": "develop",
            },
            headers=_HDR,
        )
    assert response.status_code == HTTPStatus.OK
    data = response.json()
    assert data["conflict"] is True
    assert data["conflicted_files"] == ["src/foo.py", "src/bar.py"]


@pytest.mark.asyncio
async def test_rebase_git_command_error(pm_git_client: dict) -> None:
    with patch("roboco.api.routes.git.get_git_service") as mock_get:
        svc = AsyncMock()
        svc.get_workspace = AsyncMock(return_value="/tmp/ws")
        svc.rebase = AsyncMock(side_effect=GitCommandError("rebase", "fatal error"))
        mock_get.return_value = svc
        response = await pm_git_client["client"].post(
            "/api/git/rebase",
            json={
                "project_slug": pm_git_client["project"].slug,
                "target_branch": "develop",
            },
            headers=_HDR,
        )
    assert response.status_code == HTTPStatus.INTERNAL_SERVER_ERROR


# ---------------------------------------------------------------------------
# task_id Optional — no 422 when task_id is omitted
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_commit_without_task_id_no_422(git_client: dict) -> None:
    """POST /commit without task_id must not return 422 (schema validation error)."""
    with patch("roboco.api.routes.git.get_git_service") as mock_get:
        svc = AsyncMock()
        svc.commit_for_task = AsyncMock(
            return_value=("abc123", "feat: add thing", 1, 5, 2)
        )
        mock_get.return_value = svc
        response = await git_client["client"].post(
            "/api/git/commit",
            json={
                "project_slug": git_client["project"].slug,
                "agent_id": str(uuid4()),
                "message": "add a new thing",
                "commit_type": "feat",
            },
            headers=_HDR,
        )
    assert response.status_code != HTTPStatus.UNPROCESSABLE_ENTITY
    assert response.status_code == HTTPStatus.OK


@pytest.mark.asyncio
async def test_push_without_task_id_no_422(git_client: dict) -> None:
    """POST /push without task_id must not return 422 (schema validation error)."""
    with patch("roboco.api.routes.git.get_git_service") as mock_get:
        svc = AsyncMock()
        svc.push_for_task = AsyncMock(return_value=("feature/x", 3))
        mock_get.return_value = svc
        response = await git_client["client"].post(
            "/api/git/push",
            json={
                "project_slug": git_client["project"].slug,
            },
            headers=_HDR,
        )
    assert response.status_code != HTTPStatus.UNPROCESSABLE_ENTITY
    assert response.status_code == HTTPStatus.OK


@pytest.mark.asyncio
async def test_create_pr_without_task_id_no_422(git_client: dict) -> None:
    """POST /pr/create without task_id must not return 422 (schema validation error)."""
    with patch("roboco.api.routes.git.get_git_service") as mock_get:
        svc = AsyncMock()
        svc.create_pr_for_task = AsyncMock(
            return_value=(
                7,
                "https://github.com/x/y/pull/7",
                "feat: add thing",
                "feat/x",
                "main",
            )
        )
        mock_get.return_value = svc
        response = await git_client["client"].post(
            "/api/git/pr/create",
            json={
                "project_slug": git_client["project"].slug,
            },
            headers=_HDR,
        )
    assert response.status_code != HTTPStatus.UNPROCESSABLE_ENTITY
    assert response.status_code == HTTPStatus.OK


@pytest.mark.asyncio
async def test_merge_pr_without_task_id_no_422(git_client: dict) -> None:
    """POST /pr/merge without task_id must not return 422 (schema validation error)."""
    with patch("roboco.api.routes.git.get_git_service") as mock_get:
        svc = AsyncMock()
        svc.merge_pr_for_task = AsyncMock(return_value=("main", "deadbeef"))
        mock_get.return_value = svc
        response = await git_client["client"].post(
            "/api/git/pr/merge",
            json={
                "project_slug": git_client["project"].slug,
                "pr_number": 99,
            },
            headers=_HDR,
        )
    assert response.status_code != HTTPStatus.UNPROCESSABLE_ENTITY
    assert response.status_code == HTTPStatus.OK


# ---------------------------------------------------------------------------
# branches/cleanup
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cleanup_branches_success(pm_git_client: dict) -> None:
    with patch("roboco.api.routes.git.get_git_service") as mock_get:
        svc = AsyncMock()
        svc.cleanup_stale_branches = AsyncMock(return_value=(3, 2, 1, 0, False, None))
        mock_get.return_value = svc
        response = await pm_git_client["client"].post(
            "/api/git/branches/cleanup",
            json={"project_slug": pm_git_client["project"].slug},
            headers=_HDR,
        )
    assert response.status_code == HTTPStatus.OK
    data = response.json()
    assert (
        data["remote_deleted"],
        data["local_deleted"],
        data["skipped"],
        data["errors"],
        data["truncated"],
    ) == (3, 2, 1, 0, False)
    svc.cleanup_stale_branches.assert_awaited_once_with(
        pm_git_client["project"].slug, after_task_id=None
    )


@pytest.mark.asyncio
async def test_cleanup_branches_reports_truncation(pm_git_client: dict) -> None:
    with patch("roboco.api.routes.git.get_git_service") as mock_get:
        svc = AsyncMock()
        svc.cleanup_stale_branches = AsyncMock(
            return_value=(200, 190, 0, 0, True, "0" * 32)
        )
        mock_get.return_value = svc
        response = await pm_git_client["client"].post(
            "/api/git/branches/cleanup",
            json={"project_slug": pm_git_client["project"].slug},
            headers=_HDR,
        )
    assert response.status_code == HTTPStatus.OK
    assert response.json()["truncated"] is True


@pytest.mark.asyncio
async def test_cleanup_branches_developer_gets_403(git_client: dict) -> None:
    """git_client carries a DEVELOPER-role agent — same role gate as /rebase."""
    with patch("roboco.api.routes.git.get_git_service") as mock_get:
        svc = AsyncMock()
        mock_get.return_value = svc
        response = await git_client["client"].post(
            "/api/git/branches/cleanup",
            json={"project_slug": git_client["project"].slug},
            headers=_HDR,
        )
    assert response.status_code == HTTPStatus.FORBIDDEN
    assert "BRANCH_CLEANUP_ROLE_RESTRICTED" in response.json()["detail"]
    mock_get.assert_not_called()


@pytest.mark.asyncio
async def test_cleanup_branches_project_not_found(pm_git_client: dict) -> None:
    response = await pm_git_client["client"].post(
        "/api/git/branches/cleanup",
        json={"project_slug": "does-not-exist"},
        headers=_HDR,
    )
    assert response.status_code == HTTPStatus.NOT_FOUND


# Re-export to keep import alive (TC reorders imports)
_ = SimpleNamespace
