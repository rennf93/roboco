"""Integration tests for the live intake chat routes (relay + message-in).

The SSE stream generator itself is unit-tested at the service layer
(``test_prompter_live``); here we exercise the HTTP contracts against an
injected registry whose container deliveries hit a mocked transport.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING, Any

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from roboco.api.routes.prompter_live import router
from roboco.services import prompter_live

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


@pytest_asyncio.fixture
async def live_client() -> AsyncIterator[dict[str, Any]]:
    def container_handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(container_handler))
    registry = prompter_live.PrompterLiveRegistry(http_client=mock_client)
    prompter_live._RegistryHolder.instance = registry  # inject the singleton

    app = FastAPI()
    app.include_router(router, prefix="/api/prompter")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield {"client": client, "registry": registry}

    prompter_live._RegistryHolder.instance = None
    await mock_client.aclose()


@pytest.mark.asyncio
async def test_relay_event_pushes_to_live_session(live_client: dict) -> None:
    client, registry = live_client["client"], live_client["registry"]
    registry.open("s1", "intake-1")

    resp = await client.post(
        "/api/prompter/live/s1/events", json={"kind": "text", "text": "hi"}
    )
    assert resp.status_code == HTTPStatus.OK
    assert resp.json() == {"pushed": True}

    # The event is now on the session's queue.
    assert registry.get("s1").queue.qsize() == 1


@pytest.mark.asyncio
async def test_relay_event_unknown_session_is_noop(live_client: dict) -> None:
    resp = await live_client["client"].post(
        "/api/prompter/live/nope/events", json={"kind": "text"}
    )
    assert resp.status_code == HTTPStatus.OK
    assert resp.json() == {"pushed": False}


@pytest.mark.asyncio
async def test_send_message_delivers_to_container(live_client: dict) -> None:
    client, registry = live_client["client"], live_client["registry"]
    registry.open("s1", "intake-1")

    resp = await client.post(
        "/api/prompter/live/s1/messages", json={"text": "hello there"}
    )
    assert resp.status_code == HTTPStatus.OK
    assert resp.json() == {"delivered": True}


@pytest.mark.asyncio
async def test_send_message_unknown_session_404(live_client: dict) -> None:
    resp = await live_client["client"].post(
        "/api/prompter/live/nope/messages", json={"text": "hi"}
    )
    assert resp.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.asyncio
async def test_send_message_requires_text(live_client: dict) -> None:
    live_client["registry"].open("s1", "intake-1")
    resp = await live_client["client"].post(
        "/api/prompter/live/s1/messages", json={"text": ""}
    )
    assert resp.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
