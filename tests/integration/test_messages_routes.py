"""Messages API route coverage."""

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
from roboco.api.deps import get_current_agent_id, get_db
from roboco.api.routes.messages import router as messages_router
from roboco.api.schemas.messages import MessageResponse
from roboco.db.tables import AgentTable
from roboco.models import AgentRole, AgentStatus, Team
from roboco.models.base import MessageType
from roboco.services.base import NotFoundError

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

    async def _override_db() -> AsyncIterator[AsyncSession]:
        yield db_session

    async def _override_agent_id() -> UUID:
        return cast("UUID", agent.id)

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
    assert response.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.asyncio
async def test_get_message_not_found(messages_client: dict) -> None:
    client = messages_client["client"]
    response = await client.get(f"/api/messages/{uuid4()}", headers=_HDR)
    assert response.status_code == HTTPStatus.NOT_FOUND


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
    assert response.status_code in (
        HTTPStatus.BAD_REQUEST,
        HTTPStatus.NOT_FOUND,
        HTTPStatus.UNPROCESSABLE_ENTITY,
    )


@pytest.mark.asyncio
async def test_edit_message_not_found(messages_client: dict) -> None:
    client = messages_client["client"]
    response = await client.patch(
        f"/api/messages/{uuid4()}",
        json={"new_content": "edited"},
        headers=_HDR,
    )
    assert response.status_code in (
        HTTPStatus.NOT_FOUND,
        HTTPStatus.UNPROCESSABLE_ENTITY,
    )


@pytest.mark.asyncio
async def test_delete_message_not_found(messages_client: dict) -> None:
    client = messages_client["client"]
    response = await client.delete(f"/api/messages/{uuid4()}", headers=_HDR)
    assert response.status_code in (HTTPStatus.NO_CONTENT, HTTPStatus.NOT_FOUND)


# ---------------------------------------------------------------------------
# Service-level tests via mocking
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_messages_success_empty(messages_client: dict) -> None:
    client = messages_client["client"]
    with patch("roboco.api.routes.messages.get_messaging_service") as mock_get:
        instance = AsyncMock()
        instance.list_messages_for_session = AsyncMock(return_value=([], False))
        mock_get.return_value = instance
        response = await client.get(f"/api/messages?session_id={uuid4()}", headers=_HDR)
    assert response.status_code == HTTPStatus.OK


@pytest.mark.asyncio
async def test_get_message_after_send_failure(messages_client: dict) -> None:
    """Get-message NotFoundError path."""
    client = messages_client["client"]
    with patch("roboco.api.routes.messages.get_messaging_service") as mock_get:
        instance = AsyncMock()
        instance.get_message_or_raise = AsyncMock(side_effect=NotFoundError("missing"))
        mock_get.return_value = instance
        response = await client.get(f"/api/messages/{uuid4()}", headers=_HDR)
    assert response.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.asyncio
async def test_send_message_value_error(messages_client: dict) -> None:
    client = messages_client["client"]
    with patch("roboco.api.routes.messages.get_messaging_service") as mock_get:
        instance = AsyncMock()
        instance.send_message = AsyncMock(side_effect=ValueError("session bad"))
        mock_get.return_value = instance
        response = await client.post(
            "/api/messages",
            json={
                "session_id": str(uuid4()),
                "content": "hi",
                "type": "dialogue",
            },
            headers=_HDR,
        )
    assert response.status_code == HTTPStatus.BAD_REQUEST


@pytest.mark.asyncio
async def test_edit_message_permission_error(messages_client: dict) -> None:
    client = messages_client["client"]
    with patch("roboco.api.routes.messages.get_messaging_service") as mock_get:
        instance = AsyncMock()
        instance.edit_message_or_raise = AsyncMock(
            side_effect=PermissionError("not yours")
        )
        mock_get.return_value = instance
        response = await client.patch(
            f"/api/messages/{uuid4()}",
            json={"content": "edited"},
            headers=_HDR,
        )
    assert response.status_code in (
        HTTPStatus.FORBIDDEN,
        HTTPStatus.UNPROCESSABLE_ENTITY,
    )


@pytest.mark.asyncio
async def test_delete_message_permission_error(messages_client: dict) -> None:
    client = messages_client["client"]
    with patch("roboco.api.routes.messages.get_messaging_service") as mock_get:
        instance = AsyncMock()
        instance.delete_message_or_raise = AsyncMock(
            side_effect=PermissionError("not yours")
        )
        mock_get.return_value = instance
        response = await client.delete(f"/api/messages/{uuid4()}", headers=_HDR)
    assert response.status_code == HTTPStatus.FORBIDDEN


@pytest.mark.asyncio
async def test_get_message_returns_response_when_found(messages_client: dict) -> None:
    """Line 79: get_message_or_raise returns a row → message_to_response (200)."""

    client = messages_client["client"]
    fake_msg = AsyncMock()
    msg_id = uuid4()
    fake_msg.id = msg_id
    fake_response = MessageResponse(
        id=msg_id,
        agent_id=messages_client["agent"].id,
        channel_id=uuid4(),
        group_id=uuid4(),
        session_id=uuid4(),
        type=MessageType.DIALOGUE,
        content="hello",
        content_length=5,
        is_reply=False,
        reply_to=None,
        mentions=[],
        task_id=None,
        commit_ref=None,
        timestamp=datetime.now(UTC),
        edited_at=None,
        was_edited=False,
    )
    with patch("roboco.api.routes.messages.get_messaging_service") as mock_get:
        instance = AsyncMock()
        instance.get_message_or_raise = AsyncMock(return_value=fake_msg)
        mock_get.return_value = instance
        with patch(
            "roboco.api.routes.messages.message_to_response",
            return_value=fake_response,
        ):
            response = await client.get(f"/api/messages/{fake_msg.id}", headers=_HDR)
    assert response.status_code == HTTPStatus.OK


@pytest.mark.asyncio
async def test_send_message_success_returns_201(messages_client: dict) -> None:
    """Line 113: success returns the message_to_response."""

    fake_msg = AsyncMock()
    fake_response = MessageResponse(
        id=uuid4(),
        agent_id=messages_client["agent"].id,
        channel_id=uuid4(),
        group_id=uuid4(),
        session_id=uuid4(),
        type=MessageType.DIALOGUE,
        content="hi",
        content_length=2,
        is_reply=False,
        reply_to=None,
        mentions=[],
        task_id=None,
        commit_ref=None,
        timestamp=datetime.now(UTC),
        edited_at=None,
        was_edited=False,
    )
    client = messages_client["client"]
    with patch("roboco.api.routes.messages.get_messaging_service") as mock_get:
        instance = AsyncMock()
        instance.send_message = AsyncMock(return_value=fake_msg)
        mock_get.return_value = instance
        with patch(
            "roboco.api.routes.messages.message_to_response",
            return_value=fake_response,
        ):
            response = await client.post(
                "/api/messages",
                json={
                    "session_id": str(uuid4()),
                    "content": "hello",
                    "type": "dialogue",
                },
                headers=_HDR,
            )
    assert response.status_code == HTTPStatus.CREATED


@pytest.mark.asyncio
async def test_edit_message_not_found_404(messages_client: dict) -> None:
    """Line 138: edit raises NotFoundError → 404."""
    client = messages_client["client"]
    with patch("roboco.api.routes.messages.get_messaging_service") as mock_get:
        instance = AsyncMock()
        instance.edit_message_or_raise = AsyncMock(side_effect=NotFoundError("missing"))
        mock_get.return_value = instance
        response = await client.patch(
            f"/api/messages/{uuid4()}",
            json={"content": "edited"},
            headers=_HDR,
        )
    assert response.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.asyncio
async def test_edit_message_success_returns_response(messages_client: dict) -> None:
    """Line 141: edit succeeds → message_to_response with was_edited=True."""

    fake_msg = AsyncMock()
    fake_response = MessageResponse(
        id=uuid4(),
        agent_id=messages_client["agent"].id,
        channel_id=uuid4(),
        group_id=uuid4(),
        session_id=uuid4(),
        type=MessageType.DIALOGUE,
        content="edited",
        content_length=6,
        is_reply=False,
        reply_to=None,
        mentions=[],
        task_id=None,
        commit_ref=None,
        timestamp=datetime.now(UTC),
        edited_at=datetime.now(UTC),
        was_edited=True,
    )
    client = messages_client["client"]
    with patch("roboco.api.routes.messages.get_messaging_service") as mock_get:
        instance = AsyncMock()
        instance.edit_message_or_raise = AsyncMock(return_value=fake_msg)
        mock_get.return_value = instance
        with patch(
            "roboco.api.routes.messages.message_to_response",
            return_value=fake_response,
        ):
            response = await client.patch(
                f"/api/messages/{uuid4()}",
                json={"content": "edited"},
                headers=_HDR,
            )
    assert response.status_code == HTTPStatus.OK
