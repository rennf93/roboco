"""Stream API route coverage."""

from __future__ import annotations

from http import HTTPStatus
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from roboco.api.deps import get_agent_context, get_current_agent_id, get_db
from roboco.api.routes.stream import router as stream_router
from roboco.db.tables import AgentTable
from roboco.models import AgentRole, AgentStatus, MessageType, Team
from roboco.models.permissions import AgentContext

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession


@pytest_asyncio.fixture
async def stream_client(
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
    # Init transcription/extraction state attributes (None by default)
    app.state.transcription = None
    app.state.extraction = None
    app.include_router(stream_router, prefix="/api/stream")

    async def _override_db():
        yield db_session

    async def _override_agent_id():
        return agent.id

    async def _override_agent() -> AgentContext:
        return AgentContext(
            agent_id=agent.id, role=AgentRole.DEVELOPER, team=Team.BACKEND
        )

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_agent_id] = _override_agent_id
    app.dependency_overrides[get_agent_context] = _override_agent

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield {"client": client, "agent": agent, "app": app}
    app.dependency_overrides.clear()


_HDR = {"X-Agent-ID": str(uuid4()), "X-Agent-Role": "developer"}


# ---------------------------------------------------------------------------
# /chunk
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chunk_no_transcription(stream_client: dict) -> None:
    response = await stream_client["client"].post(
        "/api/stream/chunk",
        json={
            "channel_id": str(uuid4()),
            "session_id": str(uuid4()),
            "chunk": "hello",
        },
        headers=_HDR,
    )
    assert response.status_code == HTTPStatus.SERVICE_UNAVAILABLE


@pytest.mark.asyncio
async def test_chunk_success(stream_client: dict) -> None:
    transcription = AsyncMock()
    buffer = SimpleNamespace(char_count=10)
    transcription.process_chunk = AsyncMock(return_value=buffer)
    stream_client["app"].state.transcription = transcription
    response = await stream_client["client"].post(
        "/api/stream/chunk",
        json={
            "channel_id": str(uuid4()),
            "session_id": str(uuid4()),
            "chunk": "hello",
        },
        headers=_HDR,
    )
    assert response.status_code == HTTPStatus.ACCEPTED
    body = response.json()
    assert body["ready_for_extraction"] is True


@pytest.mark.asyncio
async def test_chunk_no_buffer(stream_client: dict) -> None:
    transcription = AsyncMock()
    transcription.process_chunk = AsyncMock(return_value=None)
    stream_client["app"].state.transcription = transcription
    response = await stream_client["client"].post(
        "/api/stream/chunk",
        json={
            "channel_id": str(uuid4()),
            "session_id": str(uuid4()),
            "chunk": "hi",
        },
        headers=_HDR,
    )
    assert response.status_code == HTTPStatus.ACCEPTED
    assert response.json()["buffer_size"] == 0


# ---------------------------------------------------------------------------
# /complete
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_complete_no_transcription(stream_client: dict) -> None:
    response = await stream_client["client"].post(
        "/api/stream/complete",
        json={"session_id": str(uuid4())},
        headers=_HDR,
    )
    assert response.status_code == HTTPStatus.SERVICE_UNAVAILABLE


@pytest.mark.asyncio
async def test_complete_success(stream_client: dict) -> None:
    transcription = AsyncMock()
    transcription.process_stream_complete = AsyncMock(return_value=SimpleNamespace())
    transcription.flush_buffer = AsyncMock(return_value="some content")
    stream_client["app"].state.transcription = transcription
    response = await stream_client["client"].post(
        "/api/stream/complete",
        json={"session_id": str(uuid4())},
        headers=_HDR,
    )
    assert response.status_code == HTTPStatus.OK
    assert response.json()["status"] == "completed"


@pytest.mark.asyncio
async def test_complete_no_buffer(stream_client: dict) -> None:
    transcription = AsyncMock()
    transcription.process_stream_complete = AsyncMock(return_value=None)
    stream_client["app"].state.transcription = transcription
    response = await stream_client["client"].post(
        "/api/stream/complete",
        json={"session_id": str(uuid4())},
        headers=_HDR,
    )
    assert response.status_code == HTTPStatus.OK
    assert response.json()["status"] == "no_content"


# ---------------------------------------------------------------------------
# /extract
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_extract_no_extraction(stream_client: dict) -> None:
    response = await stream_client["client"].post(
        "/api/stream/extract",
        json={
            "channel_id": str(uuid4()),
            "session_id": str(uuid4()),
            "group_id": str(uuid4()),
            "content": "x",
        },
        headers=_HDR,
    )
    assert response.status_code == HTTPStatus.SERVICE_UNAVAILABLE


@pytest.mark.asyncio
async def test_extract_success(stream_client: dict) -> None:
    extraction = AsyncMock()
    msg = SimpleNamespace(
        id=uuid4(),
        type=MessageType.DIALOGUE,
        content="hi",
        content_length=2,
        confidence=0.99,
    )
    extraction.process_buffer = AsyncMock(
        return_value=SimpleNamespace(
            message_count=1,
            messages=[msg],
            types_extracted=[MessageType.DIALOGUE],
        )
    )
    stream_client["app"].state.extraction = extraction
    response = await stream_client["client"].post(
        "/api/stream/extract",
        json={
            "channel_id": str(uuid4()),
            "session_id": str(uuid4()),
            "group_id": str(uuid4()),
            "content": "x",
        },
        headers=_HDR,
    )
    assert response.status_code == HTTPStatus.OK


# ---------------------------------------------------------------------------
# /stats
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stats_no_transcription(stream_client: dict) -> None:
    response = await stream_client["client"].get("/api/stream/stats", headers=_HDR)
    assert response.status_code == HTTPStatus.SERVICE_UNAVAILABLE


@pytest.mark.asyncio
async def test_stats_success(stream_client: dict) -> None:
    transcription = MagicMock()
    transcription.get_stats = MagicMock(
        return_value={
            "active_agents": 0,
            "total_buffers": 0,
            "total_buffered_chars": 0,
            "running": True,
        }
    )
    stream_client["app"].state.transcription = transcription
    response = await stream_client["client"].get("/api/stream/stats", headers=_HDR)
    assert response.status_code == HTTPStatus.OK


# ---------------------------------------------------------------------------
# /permissions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_my_permissions(stream_client: dict) -> None:
    response = await stream_client["client"].get(
        "/api/stream/permissions", headers=_HDR
    )
    assert response.status_code == HTTPStatus.OK


@pytest.mark.asyncio
async def test_check_channel_permission(stream_client: dict) -> None:
    response = await stream_client["client"].get(
        "/api/stream/permissions/channel/backend-cell", headers=_HDR
    )
    assert response.status_code == HTTPStatus.OK
    body = response.json()
    assert "can_read" in body
    assert "can_write" in body
