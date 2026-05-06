"""Health route coverage."""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from roboco.api.routes.health import router as health_router

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


@pytest_asyncio.fixture
async def health_client() -> AsyncIterator[AsyncClient]:
    app = FastAPI()
    app.include_router(health_router)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.mark.asyncio
async def test_health_check_returns_ok(health_client: AsyncClient) -> None:
    response = await health_client.get("/health")
    assert response.status_code == HTTPStatus.OK
    body = response.json()
    assert body["status"] == "ok"


@pytest.mark.asyncio
async def test_readiness_check_all_healthy(health_client: AsyncClient) -> None:
    """When DB + Redis are healthy → status: ok (line 41-46)."""
    with (
        patch(
            "roboco.api.routes.health.check_database",
            AsyncMock(return_value=("connected", True)),
        ),
        patch(
            "roboco.api.routes.health.check_redis",
            AsyncMock(return_value=("connected", True)),
        ),
    ):
        response = await health_client.get("/ready")
    assert response.status_code == HTTPStatus.OK
    body = response.json()
    assert body["status"] == "ok"
    assert body["database"] == "connected"
    assert body["redis"] == "connected"


@pytest.mark.asyncio
async def test_readiness_check_degraded(health_client: AsyncClient) -> None:
    """When DB is down → status: degraded."""
    with (
        patch(
            "roboco.api.routes.health.check_database",
            AsyncMock(return_value=("disconnected", False)),
        ),
        patch(
            "roboco.api.routes.health.check_redis",
            AsyncMock(return_value=("connected", True)),
        ),
    ):
        response = await health_client.get("/ready")
    assert response.status_code == HTTPStatus.OK
    body = response.json()
    assert body["status"] == "degraded"
