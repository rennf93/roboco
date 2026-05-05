"""Messages API route coverage."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from roboco.api.deps import get_current_agent_id, get_db
from roboco.api.routes.messages import router as messages_router
from roboco.db.tables import AgentTable
from roboco.models import AgentRole, AgentStatus, Team

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession


@pytest_asyncio.fixture
async def messages_client(
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
    app.include_router(messages_router, prefix="/api/messages")

    async def _override_db():
        yield db_session

    async def _override_agent_id():
        return agent.id

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_agent_id] = _override_agent_id

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield {"client": client, "agent": agent}
    app.dependency_overrides.clear()


_HDR = {"X-Agent-ID": str(uuid4()), "X-Agent-Role": "developer"}


@pytest.mark.asyncio
async def test_list_messages_unknown_session(messages_client: dict) -> None:
    client = messages_client["client"]
    response = await client.get(f"/api/messages?session_id={uuid4()}", headers=_HDR)
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_get_message_not_found(messages_client: dict) -> None:
    client = messages_client["client"]
    response = await client.get(f"/api/messages/{uuid4()}", headers=_HDR)
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_send_message_unknown_session(messages_client: dict) -> None:
    client = messages_client["client"]
    response = await client.post(
        "/api/messages",
        json={
            "session_id": str(uuid4()),
            "content": "hello",
        },
        headers=_HDR,
    )
    # 400 if session not found / 422 if validation
    assert response.status_code in (400, 404, 422)


@pytest.mark.asyncio
async def test_edit_message_not_found(messages_client: dict) -> None:
    client = messages_client["client"]
    response = await client.patch(
        f"/api/messages/{uuid4()}",
        json={"new_content": "edited"},
        headers=_HDR,
    )
    assert response.status_code in (404, 422)


@pytest.mark.asyncio
async def test_delete_message_not_found(messages_client: dict) -> None:
    client = messages_client["client"]
    response = await client.delete(f"/api/messages/{uuid4()}", headers=_HDR)
    assert response.status_code in (204, 404)
