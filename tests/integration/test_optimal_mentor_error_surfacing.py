"""The mentor route must surface (log) the real upstream error, not mask it.

Previously an exception raised inside ``mentor.ask`` escaped the route as a
bare 500 whose body carried no diagnosable cause — agents saw only a generic
mask. The route must now log the true error and return a clean envelope whose
``detail`` names the real cause.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from roboco.api.deps import get_agent_context
from roboco.api.routes.optimal import router as optimal_router
from roboco.models import AgentRole
from roboco.models.permissions import AgentContext

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


_HDR = {"X-Agent-ID": str(uuid4()), "X-Agent-Role": "ceo"}


@pytest_asyncio.fixture
async def optimal_client() -> AsyncIterator[AsyncClient]:
    app = FastAPI()
    app.include_router(optimal_router, prefix="/api/optimal")

    async def _override_agent() -> AgentContext:
        return AgentContext(agent_id=uuid4(), role=AgentRole.CEO, team=None)

    app.dependency_overrides[get_agent_context] = _override_agent
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_mentor_ask_logs_and_surfaces_real_upstream_error(
    optimal_client: AsyncClient,
) -> None:
    mentor = AsyncMock()
    mentor._optimal_service = object()  # already initialized
    mentor.ask = AsyncMock(side_effect=RuntimeError("ollama connection refused"))

    with (
        patch("roboco.api.routes.optimal.get_mentor_service") as mock_mentor,
        patch("roboco.api.routes.optimal.get_optimal_service") as mock_get,
        patch("roboco.api.routes.optimal.logger") as mock_logger,
    ):
        mock_mentor.return_value = mentor
        mock_get.return_value = AsyncMock()
        response = await optimal_client.post(
            "/api/optimal/mentor/ask",
            json={"question": "What"},
            headers=_HDR,
        )

    # The route must not 200 on a failed ask.
    assert response.status_code in (
        HTTPStatus.INTERNAL_SERVER_ERROR,
        HTTPStatus.SERVICE_UNAVAILABLE,
    )
    # The real cause must be diagnosable in the response body, not masked.
    assert "ollama connection refused" in response.text

    # The real error must be logged (so it is diagnosable server-side even if
    # the envelope is later sanitized).
    logged_text = "".join(
        str(call.args) + str(call.kwargs)
        for call in (
            *mock_logger.error.call_args_list,
            *mock_logger.exception.call_args_list,
        )
    )
    assert "ollama connection refused" in logged_text
