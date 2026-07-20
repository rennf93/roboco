"""GitHub App route coverage — CEO-only credentials + installation/repo listing.

Mirrors ``test_x_routes.py``'s credentials section; the two listing routes
back the New Project dialog's "Select repo" picker and are covered against a
mocked ``github_app_auth`` (network calls are covered directly in
``test_github_app_auth.py``).
"""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from roboco.api.deps import get_agent_context, get_db
from roboco.api.routes.github_app import router as github_app_router
from roboco.models import AgentRole
from roboco.models.permissions import AgentContext
from roboco.services.github_app_auth import (
    GitHubAppAPIError,
    GitHubAppNotConfiguredError,
    Installation,
    InstallationRepo,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession


def _build_app(db_session: AsyncSession, role: AgentRole, agent_id: UUID) -> FastAPI:
    app = FastAPI()
    app.include_router(github_app_router, prefix="/api/github-app")

    async def _override_db() -> AsyncIterator[AsyncSession]:
        yield db_session

    async def _override_agent() -> AgentContext:
        return AgentContext(agent_id=agent_id, role=role, team=None)

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_agent_context] = _override_agent
    return app


@pytest_asyncio.fixture
async def ceo_client(db_session: AsyncSession) -> AsyncIterator[AsyncClient]:
    app = _build_app(db_session, AgentRole.CEO, uuid4())
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_credentials_default_is_unset(ceo_client: AsyncClient) -> None:
    resp = await ceo_client.get("/api/github-app/credentials")
    assert resp.status_code == HTTPStatus.OK
    assert resp.json()["has_credentials"] is False


@pytest.mark.asyncio
async def test_set_credentials_reports_status_never_plaintext(
    ceo_client: AsyncClient,
) -> None:
    resp = await ceo_client.put(
        "/api/github-app/credentials",
        json={
            "app_id": "123456",
            "private_key": "-----BEGIN KEY-----\nsecretpem\n-----END KEY-----",
        },
    )
    assert resp.status_code == HTTPStatus.OK
    assert resp.json() == {"has_credentials": True}
    assert "secretpem" not in resp.text

    status_resp = await ceo_client.get("/api/github-app/credentials")
    assert status_resp.json()["has_credentials"] is True


@pytest.mark.asyncio
async def test_partial_credentials_is_400(ceo_client: AsyncClient) -> None:
    resp = await ceo_client.put(
        "/api/github-app/credentials",
        json={"app_id": "123456", "private_key": ""},
    )
    assert resp.status_code == HTTPStatus.BAD_REQUEST


@pytest.mark.asyncio
async def test_clear_credentials(ceo_client: AsyncClient) -> None:
    await ceo_client.put(
        "/api/github-app/credentials",
        json={"app_id": "123456", "private_key": "pem-body"},
    )
    resp = await ceo_client.delete("/api/github-app/credentials")
    assert resp.status_code == HTTPStatus.OK
    assert resp.json() == {"has_credentials": False}

    status_resp = await ceo_client.get("/api/github-app/credentials")
    assert status_resp.json()["has_credentials"] is False


@pytest.mark.asyncio
async def test_installations_not_configured_is_409(ceo_client: AsyncClient) -> None:
    with patch(
        "roboco.api.routes.github_app.list_installations",
        AsyncMock(side_effect=GitHubAppNotConfiguredError("nope")),
    ):
        resp = await ceo_client.get("/api/github-app/installations")
    assert resp.status_code == HTTPStatus.CONFLICT


@pytest.mark.asyncio
async def test_installations_upstream_error_is_502(ceo_client: AsyncClient) -> None:
    with patch(
        "roboco.api.routes.github_app.list_installations",
        AsyncMock(side_effect=GitHubAppAPIError("boom")),
    ):
        resp = await ceo_client.get("/api/github-app/installations")
    assert resp.status_code == HTTPStatus.BAD_GATEWAY


@pytest.mark.asyncio
async def test_installations_returns_list(ceo_client: AsyncClient) -> None:
    with patch(
        "roboco.api.routes.github_app.list_installations",
        AsyncMock(return_value=[Installation(id=1, account_login="acme")]),
    ):
        resp = await ceo_client.get("/api/github-app/installations")
    assert resp.status_code == HTTPStatus.OK
    assert resp.json() == [{"id": 1, "account_login": "acme"}]


@pytest.mark.asyncio
async def test_installation_repositories_returns_list(ceo_client: AsyncClient) -> None:
    repos = [
        InstallationRepo(
            full_name="acme/widgets",
            clone_url="https://github.com/acme/widgets.git",
            private=True,
        )
    ]
    with patch(
        "roboco.api.routes.github_app.list_installation_repositories",
        AsyncMock(return_value=repos),
    ):
        resp = await ceo_client.get("/api/github-app/installations/1/repositories")
    assert resp.status_code == HTTPStatus.OK
    assert resp.json() == [
        {
            "full_name": "acme/widgets",
            "clone_url": "https://github.com/acme/widgets.git",
            "private": True,
        }
    ]


@pytest.mark.asyncio
async def test_non_ceo_is_forbidden(db_session: AsyncSession) -> None:
    app = _build_app(db_session, AgentRole.DEVELOPER, uuid4())
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        creds_resp = await client.get("/api/github-app/credentials")
        installs_resp = await client.get("/api/github-app/installations")
    assert creds_resp.status_code == HTTPStatus.FORBIDDEN
    assert installs_resp.status_code == HTTPStatus.FORBIDDEN
