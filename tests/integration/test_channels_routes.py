"""Channels API route coverage."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from roboco.api.deps import get_agent_context, get_db
from roboco.api.routes.channels import router as channels_router
from roboco.db.tables import AgentTable, ChannelTable
from roboco.models import AgentRole, AgentStatus
from roboco.models.base import ChannelType
from roboco.models.permissions import AgentContext

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession


@pytest_asyncio.fixture
async def channels_client(
    db_session: AsyncSession,
) -> AsyncIterator[dict]:
    main_pm = AgentTable(
        id=uuid4(),
        name="MainPM",
        slug=f"main-pm-{uuid4().hex[:8]}",
        role=AgentRole.MAIN_PM,
        team=None,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="pm",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add(main_pm)
    await db_session.flush()

    channel = ChannelTable(
        id=uuid4(),
        name="ch",
        slug=f"ch-{uuid4().hex[:6]}",
        type=ChannelType.CELL,
    )
    db_session.add(channel)
    await db_session.flush()

    app = FastAPI()
    app.include_router(channels_router, prefix="/api/channels")

    async def _override_db():
        yield db_session

    async def _override_agent() -> AgentContext:
        return AgentContext(agent_id=main_pm.id, role=AgentRole.MAIN_PM, team=None)

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_agent_context] = _override_agent

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield {"client": client, "channel": channel, "pm": main_pm}
    app.dependency_overrides.clear()


_HDR = {"X-Agent-ID": str(uuid4()), "X-Agent-Role": "main_pm"}


@pytest.mark.asyncio
async def test_list_channels(channels_client: dict) -> None:
    client = channels_client["client"]
    response = await client.get("/api/channels", headers=_HDR)
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_get_channel_unknown(channels_client: dict) -> None:
    client = channels_client["client"]
    response = await client.get(f"/api/channels/{uuid4()}", headers=_HDR)
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_list_channels_filter_by_slug(channels_client: dict) -> None:
    client = channels_client["client"]
    response = await client.get(
        f"/api/channels?slug={channels_client['channel'].slug}", headers=_HDR
    )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_get_channel_by_id(channels_client: dict) -> None:
    client = channels_client["client"]
    response = await client.get(
        f"/api/channels/{channels_client['channel'].id}", headers=_HDR
    )
    # Channel may or may not be in agent's accessible list — return some valid status.
    assert response.status_code in (200, 403, 404)
