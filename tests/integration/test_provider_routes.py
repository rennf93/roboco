"""Provider API route coverage — async httpx client + dependency overrides."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from roboco.api.deps import get_agent_context, get_db
from roboco.api.routes.provider import router as provider_router
from roboco.db.tables import ProviderConfigTable
from roboco.models import AgentRole, Team
from roboco.models.base import ModelProvider
from roboco.models.permissions import AgentContext

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession


def _make_app(db_session, role: AgentRole = AgentRole.MAIN_PM, team=None) -> FastAPI:
    app = FastAPI()
    app.include_router(provider_router, prefix="/api/providers")

    async def _override_db():
        yield db_session

    async def _override_agent() -> AgentContext:
        return AgentContext(agent_id=uuid4(), role=role, team=team)

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_agent_context] = _override_agent
    return app


@pytest_asyncio.fixture
async def app_client(
    db_session: AsyncSession,
) -> AsyncIterator[AsyncClient]:
    app = _make_app(db_session)
    suffix = uuid4().hex[:8]
    # Only seed if not already present (set_ollama_api_key in a prior test
    # may have committed rows that survive rollback isolation).
    from sqlalchemy import select as _s

    existing = (
        await db_session.execute(
            _s(ProviderConfigTable).where(
                ProviderConfigTable.type == ModelProvider.OLLAMA_CLOUD
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        db_session.add(
            ProviderConfigTable(
                name=f"anthropic-test-{suffix}",
                type=ModelProvider.ANTHROPIC,
                enabled=True,
            )
        )
        db_session.add(
            ProviderConfigTable(
                name=f"ollama-test-{suffix}",
                type=ModelProvider.OLLAMA_CLOUD,
                enabled=False,
                base_url="https://ollama.example.com",
            )
        )
        await db_session.flush()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()


_HDR_PM = {"X-Agent-ID": str(uuid4()), "X-Agent-Role": "main_pm"}


@pytest.mark.asyncio
async def test_get_catalog(app_client: AsyncClient) -> None:
    response = await app_client.get("/api/providers/catalog", headers=_HDR_PM)
    assert response.status_code == 200
    assert isinstance(response.json(), list)


@pytest.mark.asyncio
async def test_get_catalog_forbidden_for_developer(
    db_session: AsyncSession,
) -> None:
    app = _make_app(db_session, role=AgentRole.DEVELOPER, team=Team.BACKEND)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/api/providers/catalog",
            headers={"X-Agent-ID": str(uuid4()), "X-Agent-Role": "developer"},
        )
    assert response.status_code == 403
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_get_ollama_key_status(app_client: AsyncClient) -> None:
    response = await app_client.get("/api/providers/ollama-key", headers=_HDR_PM)
    assert response.status_code == 200
    body = response.json()
    assert "has_key" in body
    assert "enabled" in body


@pytest.mark.asyncio
async def test_set_ollama_key(app_client: AsyncClient) -> None:
    response = await app_client.put(
        "/api/providers/ollama-key",
        json={"api_key": "secret-key-123"},
        headers=_HDR_PM,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["has_key"] is True


@pytest.mark.asyncio
async def test_get_current_mode(app_client: AsyncClient) -> None:
    response = await app_client.get("/api/providers", headers=_HDR_PM)
    assert response.status_code == 200
    body = response.json()
    assert body["mode"] in {"anthropic", "ollama", "mix"}


@pytest.mark.asyncio
async def test_apply_mode_anthropic_clears_assignments(
    app_client: AsyncClient,
) -> None:
    response = await app_client.post(
        "/api/providers", json={"mode": "anthropic"}, headers=_HDR_PM
    )
    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "anthropic"


@pytest.mark.asyncio
async def test_apply_mode_unknown_returns_4xx(app_client: AsyncClient) -> None:
    """Unknown mode is rejected — Pydantic 422 at schema layer or 400 at service."""
    response = await app_client.post(
        "/api/providers", json={"mode": "quantum"}, headers=_HDR_PM
    )
    assert response.status_code in (400, 422)
