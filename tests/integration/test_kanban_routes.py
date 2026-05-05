"""Kanban API route coverage."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from roboco.api.deps import get_db
from roboco.api.routes.kanban import router as kanban_router

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession


@pytest_asyncio.fixture
async def kanban_client(
    db_session: AsyncSession,
) -> AsyncIterator[AsyncClient]:
    app = FastAPI()
    app.include_router(kanban_router, prefix="/api/kanban")

    async def _override_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_get_dev_board(kanban_client: AsyncClient) -> None:
    response = await kanban_client.get("/api/kanban/dev/backend")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_get_qa_board(kanban_client: AsyncClient) -> None:
    response = await kanban_client.get("/api/kanban/qa/backend")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_get_documenter_board(kanban_client: AsyncClient) -> None:
    response = await kanban_client.get("/api/kanban/documenter/backend")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_get_pm_board(kanban_client: AsyncClient) -> None:
    response = await kanban_client.get("/api/kanban/pm/backend")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_get_main_pm_board(kanban_client: AsyncClient) -> None:
    response = await kanban_client.get("/api/kanban/main-pm")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_get_main_pm_board_flat(kanban_client: AsyncClient) -> None:
    response = await kanban_client.get("/api/kanban/main-pm?flat=true")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_get_board_kanban(kanban_client: AsyncClient) -> None:
    response = await kanban_client.get("/api/kanban/board")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_get_kanban_stats(kanban_client: AsyncClient) -> None:
    response = await kanban_client.get("/api/kanban/stats")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_get_dev_board_with_swimlane(kanban_client: AsyncClient) -> None:
    response = await kanban_client.get("/api/kanban/dev/backend?swimlane_by=priority")
    assert response.status_code == 200
