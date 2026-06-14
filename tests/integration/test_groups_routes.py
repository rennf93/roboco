"""Groups API route coverage."""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from http import HTTPStatus
from typing import TYPE_CHECKING, cast
from unittest.mock import patch
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from roboco.api.deps import get_agent_context, get_db
from roboco.api.routes.groups import router as groups_router
from roboco.db.tables import AgentTable, ChannelTable, GroupTable
from roboco.models import AgentRole, AgentStatus, Team
from roboco.models.base import ChannelType
from roboco.models.permissions import AgentContext

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession


@pytest_asyncio.fixture
async def groups_client(
    db_session: AsyncSession,
) -> AsyncIterator[dict]:
    pm = AgentTable(
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
    db_session.add(pm)
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
    app.include_router(groups_router, prefix="/api/groups")

    async def _override_db() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    async def _override_agent() -> AgentContext:
        return AgentContext(agent_id=cast(uuid.UUID, pm.id), role=AgentRole.MAIN_PM, team=None)

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_agent_context] = _override_agent

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield {"client": client, "channel": channel, "pm": pm}
    app.dependency_overrides.clear()


_HDR = {"X-Agent-ID": str(uuid4()), "X-Agent-Role": "main_pm"}


@pytest.mark.asyncio
async def test_create_group_main_pm(groups_client: dict) -> None:
    client = groups_client["client"]
    response = await client.post(
        "/api/groups",
        json={
            "channel_slug": groups_client["channel"].slug,
            "name": "Sprint 1",
            "hierarchy_level": 4,
            "allowed_roles": [],
        },
        headers=_HDR,
    )
    assert response.status_code == HTTPStatus.CREATED


@pytest.mark.asyncio
async def test_create_group_unknown_channel(groups_client: dict) -> None:
    client = groups_client["client"]
    response = await client.post(
        "/api/groups",
        json={
            "channel_slug": "ghost-channel",
            "name": "Sprint 1",
            "hierarchy_level": 4,
        },
        headers=_HDR,
    )
    assert response.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.asyncio
async def test_create_group_value_error_returns_400(groups_client: dict) -> None:
    """Lines 84-88: ValueError from service.create_group → 400."""

    client = groups_client["client"]
    with patch(
        "roboco.services.messaging.MessagingService.create_group",
        side_effect=ValueError("invalid"),
    ):
        response = await client.post(
            "/api/groups",
            json={
                "channel_slug": groups_client["channel"].slug,
                "name": "Sprint 1",
                "hierarchy_level": 4,
                "allowed_roles": [],
            },
            headers=_HDR,
        )
    assert response.status_code == HTTPStatus.BAD_REQUEST


@pytest.mark.asyncio
async def test_get_group_not_found(groups_client: dict) -> None:
    client = groups_client["client"]
    response = await client.get(f"/api/groups/{uuid4()}", headers=_HDR)
    assert response.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.asyncio
async def test_get_group(groups_client: dict, db_session: AsyncSession) -> None:
    client = groups_client["client"]
    group = GroupTable(
        id=uuid4(),
        name="g1",
        channel_id=groups_client["channel"].id,
        hierarchy_level=4,
    )
    db_session.add(group)
    await db_session.flush()
    response = await client.get(f"/api/groups/{group.id}", headers=_HDR)
    assert response.status_code == HTTPStatus.OK


@pytest.mark.asyncio
async def test_create_group_developer_forbidden(
    db_session: AsyncSession,
) -> None:
    """Developers can't create groups."""
    dev = AgentTable(
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
    db_session.add(dev)
    await db_session.flush()

    app = FastAPI()
    app.include_router(groups_router, prefix="/api/groups")

    async def _override_db() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    async def _override_agent() -> AgentContext:
        return AgentContext(
            agent_id=cast(uuid.UUID, dev.id), role=AgentRole.DEVELOPER, team=Team.BACKEND
        )

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_agent_context] = _override_agent

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/groups",
            json={
                "channel_slug": "backend-cell",
                "name": "x",
                "hierarchy_level": 4,
            },
            headers=_HDR,
        )
    app.dependency_overrides.clear()
    assert response.status_code == HTTPStatus.FORBIDDEN
