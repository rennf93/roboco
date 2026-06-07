"""Prompter API route coverage — chat and draft endpoints."""

from __future__ import annotations

import json
from http import HTTPStatus
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from roboco.api.deps import get_agent_context, get_db
from roboco.api.routes.prompter import router as prompter_router
from roboco.db.tables import AgentTable
from roboco.models.base import AgentRole, AgentStatus
from roboco.models.permissions import AgentContext

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession


@pytest_asyncio.fixture
async def prompter_client(
    db_session: AsyncSession,
) -> AsyncIterator[dict[str, Any]]:
    agent = AgentTable(
        id=uuid4(),
        name="DevAgent",
        slug=f"dev-agent-{uuid4().hex[:8]}",
        role=AgentRole.DEVELOPER,
        team=None,
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
    app.include_router(prompter_router, prefix="/api/prompter")

    async def _override_db() -> AsyncIterator[AsyncSession]:
        yield db_session

    async def _override_agent() -> AgentContext:
        return AgentContext(
            agent_id=agent.id,  # type: ignore[arg-type]
            role=AgentRole.DEVELOPER,
            team=None,
        )

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_agent_context] = _override_agent

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield {"client": client, "agent": agent, "db": db_session}
    app.dependency_overrides.clear()


_HDR = {"X-Agent-ID": "be-dev-1", "X-Agent-Role": "developer"}


# -----------------------------------------------------------------------------
# Chat endpoint
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_prompter_chat_success(prompter_client: dict) -> None:
    client = prompter_client["client"]

    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="Great! Let's gather requirements.")]

    with patch(
        "roboco.services.prompter.AsyncAnthropic.messages.create",
        new_callable=AsyncMock,
        return_value=mock_response,
    ):
        response = await client.post(
            "/api/prompter/chat",
            json={
                "messages": [{"role": "user", "content": "I need a new feature"}],
            },
            headers=_HDR,
        )

    assert response.status_code == HTTPStatus.OK
    body = response.json()
    assert body["message"] == "Great! Let's gather requirements."
    assert body["draft_ready"] is False


@pytest.mark.asyncio
async def test_prompter_chat_draft_ready(prompter_client: dict) -> None:
    client = prompter_client["client"]

    mock_response = MagicMock()
    mock_response.content = [
        MagicMock(
            text=(
                "I have enough information. draft_ready=true."
                " Ready to generate a draft."
            )
        )
    ]

    with patch(
        "roboco.services.prompter.AsyncAnthropic.messages.create",
        new_callable=AsyncMock,
        return_value=mock_response,
    ):
        response = await client.post(
            "/api/prompter/chat",
            json={
                "messages": [
                    {"role": "user", "content": "I need a new feature"},
                    {"role": "assistant", "content": "Tell me more"},
                    {"role": "user", "content": "Add a login page"},
                ],
            },
            headers=_HDR,
        )

    assert response.status_code == HTTPStatus.OK
    body = response.json()
    assert body["draft_ready"] is True


@pytest.mark.asyncio
async def test_prompter_chat_llm_failure(prompter_client: dict) -> None:
    client = prompter_client["client"]

    with patch(
        "roboco.services.prompter.AsyncAnthropic.messages.create",
        new_callable=AsyncMock,
        side_effect=Exception("Anthropic API unavailable"),
    ):
        response = await client.post(
            "/api/prompter/chat",
            json={
                "messages": [{"role": "user", "content": "Hello"}],
            },
            headers=_HDR,
        )

    assert response.status_code == HTTPStatus.INTERNAL_SERVER_ERROR
    body = response.json()
    assert "LLM chat failed" in body["detail"]["message"]


# -----------------------------------------------------------------------------
# Draft endpoint
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_prompter_draft_success(prompter_client: dict) -> None:
    client = prompter_client["client"]

    draft_json = {
        "title": "Add login page",
        "description": "Implement a secure login page with email and password",
        "acceptance_criteria": [
            "User can enter email and password",
            "Invalid credentials show error message",
        ],
        "team": "frontend",
        "task_type": "code",
        "nature": "technical",
        "estimated_complexity": "medium",
        "priority": 2,
    }

    mock_response = MagicMock()
    mock_response.content = [MagicMock(text=json.dumps(draft_json))]

    with patch(
        "roboco.services.prompter.AsyncAnthropic.messages.create",
        new_callable=AsyncMock,
        return_value=mock_response,
    ):
        response = await client.post(
            "/api/prompter/draft",
            json={
                "messages": [
                    {"role": "user", "content": "I need a login page"},
                ],
            },
            headers=_HDR,
        )

    assert response.status_code == HTTPStatus.OK
    body = response.json()
    assert body["draft"]["title"] == "Add login page"
    assert body["draft"]["source"] == "prompter"
    assert body["draft"]["confirmed_by_human"] is False
    assert "reasoning" in body


@pytest.mark.asyncio
async def test_prompter_draft_invalid_json_from_llm(prompter_client: dict) -> None:
    client = prompter_client["client"]

    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="not valid json")]

    with patch(
        "roboco.services.prompter.AsyncAnthropic.messages.create",
        new_callable=AsyncMock,
        return_value=mock_response,
    ):
        response = await client.post(
            "/api/prompter/draft",
            json={
                "messages": [{"role": "user", "content": "Hello"}],
            },
            headers=_HDR,
        )

    assert response.status_code == HTTPStatus.BAD_REQUEST
    body = response.json()
    assert "Draft response was not valid JSON" in body["detail"]["message"]


@pytest.mark.asyncio
async def test_prompter_draft_schema_mismatch(prompter_client: dict) -> None:
    client = prompter_client["client"]

    # Missing required fields like acceptance_criteria and team
    bad_draft = {
        "title": "x",
        "description": "too short",
    }

    mock_response = MagicMock()
    mock_response.content = [MagicMock(text=json.dumps(bad_draft))]

    with patch(
        "roboco.services.prompter.AsyncAnthropic.messages.create",
        new_callable=AsyncMock,
        return_value=mock_response,
    ):
        response = await client.post(
            "/api/prompter/draft",
            json={
                "messages": [{"role": "user", "content": "Hello"}],
            },
            headers=_HDR,
        )

    assert response.status_code == HTTPStatus.INTERNAL_SERVER_ERROR
    body = response.json()
    assert "draft_schema_error" in body["detail"]["error"]


@pytest.mark.asyncio
async def test_prompter_draft_llm_failure(prompter_client: dict) -> None:
    client = prompter_client["client"]

    with patch(
        "roboco.services.prompter.AsyncAnthropic.messages.create",
        new_callable=AsyncMock,
        side_effect=Exception("Anthropic API unavailable"),
    ):
        response = await client.post(
            "/api/prompter/draft",
            json={
                "messages": [{"role": "user", "content": "Hello"}],
            },
            headers=_HDR,
        )

    assert response.status_code == HTTPStatus.INTERNAL_SERVER_ERROR
    body = response.json()
    assert "LLM draft generation failed" in body["detail"]["message"]


# -----------------------------------------------------------------------------
# Validation
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_prompter_chat_empty_messages(prompter_client: dict) -> None:
    client = prompter_client["client"]
    response = await client.post(
        "/api/prompter/chat",
        json={"messages": []},
        headers=_HDR,
    )
    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


@pytest.mark.asyncio
async def test_prompter_chat_invalid_role(prompter_client: dict) -> None:
    client = prompter_client["client"]
    response = await client.post(
        "/api/prompter/chat",
        json={"messages": [{"role": "invalid", "content": "hi"}]},
        headers=_HDR,
    )
    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


@pytest.mark.asyncio
async def test_prompter_draft_empty_messages(prompter_client: dict) -> None:
    client = prompter_client["client"]
    response = await client.post(
        "/api/prompter/draft",
        json={"messages": []},
        headers=_HDR,
    )
    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
