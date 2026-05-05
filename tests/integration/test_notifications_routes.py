"""Notifications API route coverage."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from roboco.api.deps import get_agent_context, get_current_agent_id, get_db
from roboco.api.routes.notifications import router as notifications_router
from roboco.db.tables import AgentTable
from roboco.models import AgentRole, AgentStatus, Team
from roboco.models.permissions import AgentContext

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession


@pytest_asyncio.fixture
async def notif_client(
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

    app = FastAPI()
    app.include_router(notifications_router, prefix="/api/notifications")

    async def _override_db():
        yield db_session

    async def _override_agent() -> AgentContext:
        return AgentContext(
            agent_id=agent.id, role=AgentRole.DEVELOPER, team=Team.BACKEND
        )

    async def _override_agent_id():
        return agent.id

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_agent_context] = _override_agent
    app.dependency_overrides[get_current_agent_id] = _override_agent_id

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield {"client": client, "agent": agent}
    app.dependency_overrides.clear()


_HDR = {"X-Agent-ID": str(uuid4()), "X-Agent-Role": "developer"}


@pytest.mark.asyncio
async def test_list_notifications_empty(notif_client: dict) -> None:
    client = notif_client["client"]
    response = await client.get("/api/notifications", headers=_HDR)
    assert response.status_code == 200
    body = response.json()
    assert body["items"] == []
    assert body["total"] == 0


@pytest.mark.asyncio
async def test_get_notification_not_found(notif_client: dict) -> None:
    client = notif_client["client"]
    response = await client.get(f"/api/notifications/{uuid4()}", headers=_HDR)
    assert response.status_code in (404, 403)


@pytest.mark.asyncio
async def test_acknowledge_notification_not_found(notif_client: dict) -> None:
    client = notif_client["client"]
    response = await client.post(f"/api/notifications/{uuid4()}/ack", headers=_HDR)
    assert response.status_code in (404, 403)


@pytest.mark.asyncio
async def test_mark_as_read_not_found(notif_client: dict) -> None:
    client = notif_client["client"]
    response = await client.post(f"/api/notifications/{uuid4()}/read", headers=_HDR)
    assert response.status_code in (404, 403)


@pytest.mark.asyncio
async def test_list_with_filters(notif_client: dict) -> None:
    client = notif_client["client"]
    response = await client.get(
        "/api/notifications?unread_only=true&limit=20", headers=_HDR
    )
    assert response.status_code == 200
