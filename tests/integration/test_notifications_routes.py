"""Notifications API route coverage."""

from __future__ import annotations

from datetime import UTC, datetime
from http import HTTPStatus
from typing import TYPE_CHECKING, cast
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from roboco.api.deps import get_agent_context, get_current_agent_id, get_db
from roboco.api.routes.notifications import router as notifications_router
from roboco.api.schemas.notifications import NotificationResponse
from roboco.db.tables import AgentTable
from roboco.models import AgentRole, AgentStatus, Team
from roboco.models.base import NotificationPriority, NotificationType
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

    async def _override_db() -> AsyncIterator[AsyncSession]:
        yield db_session

    async def _override_agent() -> AgentContext:
        return AgentContext(
            agent_id=cast("UUID", agent.id), role=AgentRole.DEVELOPER, team=Team.BACKEND
        )

    async def _override_agent_id() -> UUID:
        return cast("UUID", agent.id)

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
    assert response.status_code == HTTPStatus.OK
    body = response.json()
    assert body["items"] == []
    assert body["total"] == 0


@pytest.mark.asyncio
async def test_get_notification_not_found(notif_client: dict) -> None:
    client = notif_client["client"]
    response = await client.get(f"/api/notifications/{uuid4()}", headers=_HDR)
    assert response.status_code in (HTTPStatus.NOT_FOUND, HTTPStatus.FORBIDDEN)


@pytest.mark.asyncio
async def test_acknowledge_notification_not_found(notif_client: dict) -> None:
    client = notif_client["client"]
    response = await client.post(f"/api/notifications/{uuid4()}/ack", headers=_HDR)
    assert response.status_code in (HTTPStatus.NOT_FOUND, HTTPStatus.FORBIDDEN)


@pytest.mark.asyncio
async def test_mark_as_read_not_found(notif_client: dict) -> None:
    client = notif_client["client"]
    response = await client.post(f"/api/notifications/{uuid4()}/read", headers=_HDR)
    assert response.status_code in (HTTPStatus.NOT_FOUND, HTTPStatus.FORBIDDEN)


@pytest.mark.asyncio
async def test_list_with_filters(notif_client: dict) -> None:
    client = notif_client["client"]
    response = await client.get(
        "/api/notifications?unread_only=true&limit=20", headers=_HDR
    )
    assert response.status_code == HTTPStatus.OK


# ---------------------------------------------------------------------------
# System role list path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_notifications_system_role(db_session: AsyncSession) -> None:
    """System role triggers list_system_notifications branch."""
    sys_agent = AgentTable(
        id=uuid4(),
        name="Sys",
        slug=f"system-{uuid4().hex[:8]}",
        role=AgentRole.SYSTEM,
        team=None,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="sys",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add(sys_agent)
    await db_session.flush()

    app = FastAPI()
    app.include_router(notifications_router, prefix="/api/notifications")

    async def _override_db() -> AsyncIterator[AsyncSession]:
        yield db_session

    async def _override_agent() -> AgentContext:
        return AgentContext(
            agent_id=cast("UUID", sys_agent.id), role=AgentRole.SYSTEM, team=None
        )

    async def _override_agent_id() -> UUID:
        return cast("UUID", sys_agent.id)

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_agent_context] = _override_agent
    app.dependency_overrides[get_current_agent_id] = _override_agent_id

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/notifications", headers=_HDR)
    app.dependency_overrides.clear()
    assert response.status_code == HTTPStatus.OK


@pytest.mark.asyncio
async def test_get_notification_permission_error(notif_client: dict) -> None:
    client = notif_client["client"]
    with patch(
        "roboco.api.routes.notifications.get_notification_delivery_service"
    ) as mock_get:
        instance = AsyncMock()
        instance.get_for_recipient_and_mark_read = AsyncMock(
            side_effect=PermissionError("denied")
        )
        mock_get.return_value = instance
        response = await client.get(f"/api/notifications/{uuid4()}", headers=_HDR)
    assert response.status_code == HTTPStatus.FORBIDDEN


@pytest.mark.asyncio
async def test_acknowledge_value_error(notif_client: dict) -> None:
    client = notif_client["client"]
    with patch(
        "roboco.api.routes.notifications.get_notification_delivery_service"
    ) as mock_get:
        instance = AsyncMock()
        instance.acknowledge_for_recipient = AsyncMock(
            side_effect=ValueError("bad data")
        )
        mock_get.return_value = instance
        response = await client.post(f"/api/notifications/{uuid4()}/ack", headers=_HDR)
    assert response.status_code == HTTPStatus.BAD_REQUEST


@pytest.mark.asyncio
async def test_mark_read_permission_error(notif_client: dict) -> None:
    client = notif_client["client"]
    with patch(
        "roboco.api.routes.notifications.get_notification_delivery_service"
    ) as mock_get:
        instance = AsyncMock()
        instance.mark_read_for_recipient = AsyncMock(
            side_effect=PermissionError("nope")
        )
        mock_get.return_value = instance
        response = await client.post(f"/api/notifications/{uuid4()}/read", headers=_HDR)
    assert response.status_code == HTTPStatus.FORBIDDEN


@pytest.mark.asyncio
async def test_get_notification_returns_response(notif_client: dict) -> None:
    """Line 98: get success → notification_to_response."""

    client = notif_client["client"]
    fake_notif = AsyncMock()
    fake_response = NotificationResponse(
        id=uuid4(),
        type=NotificationType.TASK_ASSIGNMENT,
        priority=NotificationPriority.NORMAL,
        from_agent=uuid4(),
        to_agents=[uuid4()],
        subject="x",
        body="y",
        requires_ack=False,
        is_acknowledged=False,
        is_fully_acknowledged=False,
        is_read=True,
        related_task_id=None,
        timestamp=datetime.now(UTC),
        expires_at=None,
    )
    with patch(
        "roboco.api.routes.notifications.get_notification_delivery_service"
    ) as mock_get:
        instance = AsyncMock()
        instance.get_for_recipient_and_mark_read = AsyncMock(return_value=fake_notif)
        mock_get.return_value = instance
        with patch(
            "roboco.api.routes.notifications.notification_to_response",
            return_value=fake_response,
        ):
            response = await client.get(f"/api/notifications/{uuid4()}", headers=_HDR)
    assert response.status_code == HTTPStatus.OK


@pytest.mark.asyncio
async def test_acknowledge_permission_error(notif_client: dict) -> None:
    """Line 121: PermissionError on ack → 403."""
    client = notif_client["client"]
    with patch(
        "roboco.api.routes.notifications.get_notification_delivery_service"
    ) as mock_get:
        instance = AsyncMock()
        instance.acknowledge_for_recipient = AsyncMock(
            side_effect=PermissionError("nope")
        )
        mock_get.return_value = instance
        response = await client.post(f"/api/notifications/{uuid4()}/ack", headers=_HDR)
    assert response.status_code == HTTPStatus.FORBIDDEN


@pytest.mark.asyncio
async def test_acknowledge_returns_response(notif_client: dict) -> None:
    """Line 126: ack success → notification_to_response."""

    client = notif_client["client"]
    fake_notif = AsyncMock()
    fake_response = NotificationResponse(
        id=uuid4(),
        type=NotificationType.TASK_ASSIGNMENT,
        priority=NotificationPriority.NORMAL,
        from_agent=uuid4(),
        to_agents=[uuid4()],
        subject="x",
        body="y",
        requires_ack=True,
        is_acknowledged=True,
        is_fully_acknowledged=True,
        is_read=True,
        related_task_id=None,
        timestamp=datetime.now(UTC),
        expires_at=None,
    )
    with patch(
        "roboco.api.routes.notifications.get_notification_delivery_service"
    ) as mock_get:
        instance = AsyncMock()
        instance.acknowledge_for_recipient = AsyncMock(return_value=fake_notif)
        mock_get.return_value = instance
        with patch(
            "roboco.api.routes.notifications.notification_to_response",
            return_value=fake_response,
        ):
            response = await client.post(
                f"/api/notifications/{uuid4()}/ack", headers=_HDR
            )
    assert response.status_code == HTTPStatus.OK
