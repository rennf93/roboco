"""Unit tests: task_id is Optional in git request schemas and service methods.

These tests verify:
- The four git schemas accept None task_id (no 422 on absent field).
- Existing callers that pass task_id still work (regression).
- The HTTP endpoints return 200 when task_id is omitted.

All tests run without a real database or git process.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, cast
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from roboco.api.deps import get_agent_context, get_db
from roboco.api.routes.git import router as git_router
from roboco.api.schemas.git import (
    GitCommitRequest,
    GitCreatePRRequest,
    GitMergePRRequest,
    GitPushRequest,
)
from roboco.models.base import AgentRole, Team
from roboco.models.permissions import AgentContext

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, AsyncIterator

_HTTP_UNPROCESSABLE = 422
_HTTP_OK = 200

_AGENT_ID = uuid4()
_TASK_ID = uuid4()


# ---------------------------------------------------------------------------
# Fixtures — no real DB required
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:  # type: ignore[type-arg]
    """FastAPI test client with mocked auth + DB; no Postgres needed."""
    app = FastAPI()
    app.include_router(git_router, prefix="/api/git")

    async def _mock_db() -> AsyncGenerator:  # type: ignore[type-arg]
        yield AsyncMock()  # DB session is never touched in these tests

    async def _mock_agent() -> AgentContext:
        return AgentContext(
            agent_id=cast("uuid.UUID", _AGENT_ID),
            role=AgentRole.DEVELOPER,
            team=Team.BACKEND,
        )

    app.dependency_overrides[get_db] = _mock_db
    app.dependency_overrides[get_agent_context] = _mock_agent

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


_HDR = {"X-Agent-ID": str(_AGENT_ID), "X-Agent-Role": "developer"}

# ---------------------------------------------------------------------------
# Schema unit tests — direct Pydantic validation, no HTTP needed
# ---------------------------------------------------------------------------


def test_commit_request_task_id_optional() -> None:
    """GitCommitRequest accepts a missing task_id (defaults to None)."""
    req = GitCommitRequest(
        project_slug="roboco",
        agent_id=str(_AGENT_ID),
        message="add something new here",
        commit_type="feat",
    )
    assert req.task_id is None


def test_commit_request_task_id_present() -> None:
    """GitCommitRequest still accepts an explicit task_id (regression)."""
    req = GitCommitRequest(
        project_slug="roboco",
        task_id=_TASK_ID,
        agent_id=str(_AGENT_ID),
        message="add something new here",
        commit_type="feat",
    )
    assert req.task_id == _TASK_ID


def test_push_request_task_id_optional() -> None:
    """GitPushRequest accepts a missing task_id (defaults to None)."""
    req = GitPushRequest(project_slug="roboco")
    assert req.task_id is None


def test_push_request_task_id_present() -> None:
    """GitPushRequest still accepts an explicit task_id (regression)."""
    req = GitPushRequest(project_slug="roboco", task_id=_TASK_ID)
    assert req.task_id == _TASK_ID


def test_create_pr_request_task_id_optional() -> None:
    """GitCreatePRRequest accepts a missing task_id (defaults to None)."""
    req = GitCreatePRRequest(project_slug="roboco")
    assert req.task_id is None


def test_create_pr_request_task_id_present() -> None:
    """GitCreatePRRequest still accepts an explicit task_id (regression)."""
    req = GitCreatePRRequest(project_slug="roboco", task_id=_TASK_ID)
    assert req.task_id == _TASK_ID


def test_merge_pr_request_task_id_optional() -> None:
    """GitMergePRRequest accepts a missing task_id (defaults to None)."""
    req = GitMergePRRequest(project_slug="roboco", pr_number=42)
    assert req.task_id is None


def test_merge_pr_request_task_id_present() -> None:
    """GitMergePRRequest still accepts an explicit task_id (regression)."""
    req = GitMergePRRequest(project_slug="roboco", pr_number=42, task_id=_TASK_ID)
    assert req.task_id == _TASK_ID


# ---------------------------------------------------------------------------
# HTTP endpoint tests — verify no 422 when task_id is absent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_commit_without_task_id_returns_200_not_422(
    client: AsyncClient,
) -> None:
    """POST /commit without task_id must not return 422 (schema error)."""
    with patch("roboco.api.routes.git.get_git_service") as mock_svc:
        svc = AsyncMock()
        svc.commit_for_task = AsyncMock(
            return_value=("deadbeef", "feat: add something", 1, 5, 2)
        )
        mock_svc.return_value = svc
        response = await client.post(
            "/api/git/commit",
            json={
                "project_slug": "roboco",
                "agent_id": str(_AGENT_ID),
                "message": "add something new",
                "commit_type": "feat",
                # task_id intentionally omitted
            },
            headers=_HDR,
        )
    assert response.status_code != _HTTP_UNPROCESSABLE, (
        f"Expected no 422, got {response.status_code}: {response.text}"
    )
    assert response.status_code == _HTTP_OK


@pytest.mark.asyncio
async def test_push_without_task_id_returns_200_not_422(
    client: AsyncClient,
) -> None:
    """POST /push without task_id must not return 422 (schema error)."""
    with patch("roboco.api.routes.git.get_git_service") as mock_svc:
        svc = AsyncMock()
        svc.push_for_task = AsyncMock(return_value=("feature/x", 3))
        mock_svc.return_value = svc
        response = await client.post(
            "/api/git/push",
            json={
                "project_slug": "roboco",
                # task_id intentionally omitted
            },
            headers=_HDR,
        )
    assert response.status_code != _HTTP_UNPROCESSABLE, (
        f"Expected no 422, got {response.status_code}: {response.text}"
    )
    assert response.status_code == _HTTP_OK


@pytest.mark.asyncio
async def test_create_pr_without_task_id_returns_200_not_422(
    client: AsyncClient,
) -> None:
    """POST /pr/create without task_id must not return 422 (schema error)."""
    with patch("roboco.api.routes.git.get_git_service") as mock_svc:
        svc = AsyncMock()
        svc.create_pr_for_task = AsyncMock(
            return_value=(
                7,
                "https://github.com/x/y/pull/7",
                "feat: add thing",
                "feature/x",
                "main",
            )
        )
        mock_svc.return_value = svc
        response = await client.post(
            "/api/git/pr/create",
            json={
                "project_slug": "roboco",
                # task_id intentionally omitted
            },
            headers=_HDR,
        )
    assert response.status_code != _HTTP_UNPROCESSABLE, (
        f"Expected no 422, got {response.status_code}: {response.text}"
    )
    assert response.status_code == _HTTP_OK


@pytest.mark.asyncio
async def test_merge_pr_without_task_id_returns_200_not_422(
    client: AsyncClient,
) -> None:
    """POST /pr/merge without task_id must not return 422 (schema error)."""
    with patch("roboco.api.routes.git.get_git_service") as mock_svc:
        svc = AsyncMock()
        svc.merge_pr_for_task = AsyncMock(return_value=("main", "deadbeef"))
        mock_svc.return_value = svc
        response = await client.post(
            "/api/git/pr/merge",
            json={
                "project_slug": "roboco",
                "pr_number": 99,
                # task_id intentionally omitted
            },
            headers=_HDR,
        )
    assert response.status_code != _HTTP_UNPROCESSABLE, (
        f"Expected no 422, got {response.status_code}: {response.text}"
    )
    assert response.status_code == _HTTP_OK


# ---------------------------------------------------------------------------
# Regression: existing callers that pass task_id still get 200
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_commit_with_task_id_still_works(client: AsyncClient) -> None:
    """POST /commit with task_id (regression) must still return 200."""
    with patch("roboco.api.routes.git.get_git_service") as mock_svc:
        svc = AsyncMock()
        svc.commit_for_task = AsyncMock(
            return_value=("abc123", "fix(auth): correct thing", 2, 10, 3)
        )
        mock_svc.return_value = svc
        response = await client.post(
            "/api/git/commit",
            json={
                "project_slug": "roboco",
                "task_id": str(_TASK_ID),
                "agent_id": str(_AGENT_ID),
                "message": "correct the auth flow",
                "commit_type": "fix",
            },
            headers=_HDR,
        )
    assert response.status_code == _HTTP_OK


@pytest.mark.asyncio
async def test_push_with_task_id_still_works(client: AsyncClient) -> None:
    """POST /push with task_id (regression) must still return 200."""
    with patch("roboco.api.routes.git.get_git_service") as mock_svc:
        svc = AsyncMock()
        svc.push_for_task = AsyncMock(return_value=("feature/x", 2))
        mock_svc.return_value = svc
        response = await client.post(
            "/api/git/push",
            json={
                "project_slug": "roboco",
                "task_id": str(_TASK_ID),
            },
            headers=_HDR,
        )
    assert response.status_code == _HTTP_OK


@pytest.mark.asyncio
async def test_create_pr_with_task_id_still_works(client: AsyncClient) -> None:
    """POST /pr/create with task_id (regression) must still return 200."""
    with patch("roboco.api.routes.git.get_git_service") as mock_svc:
        svc = AsyncMock()
        svc.create_pr_for_task = AsyncMock(
            return_value=(5, "https://github.com/x/y/pull/5", "T", "feat", "main")
        )
        mock_svc.return_value = svc
        response = await client.post(
            "/api/git/pr/create",
            json={
                "project_slug": "roboco",
                "task_id": str(_TASK_ID),
            },
            headers=_HDR,
        )
    assert response.status_code == _HTTP_OK


@pytest.mark.asyncio
async def test_merge_pr_with_task_id_still_works(client: AsyncClient) -> None:
    """POST /pr/merge with task_id (regression) must still return 200."""
    with patch("roboco.api.routes.git.get_git_service") as mock_svc:
        svc = AsyncMock()
        svc.merge_pr_for_task = AsyncMock(return_value=("main", "cafebabe"))
        mock_svc.return_value = svc
        response = await client.post(
            "/api/git/pr/merge",
            json={
                "project_slug": "roboco",
                "pr_number": 5,
                "task_id": str(_TASK_ID),
            },
            headers=_HDR,
        )
    assert response.status_code == _HTTP_OK
