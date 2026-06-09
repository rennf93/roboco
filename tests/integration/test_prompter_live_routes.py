"""Integration tests for the live intake chat routes (start/stop + relay + msg).

The SSE stream generator itself is unit-tested at the service layer
(``test_prompter_live``); here we exercise the HTTP contracts against an
injected registry whose container deliveries hit a mocked transport, and the
start/stop routes against a fake orchestrator.
"""

from __future__ import annotations

from http import HTTPStatus
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import patch
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from roboco.api import deps
from roboco.api.routes.prompter_live import router
from roboco.db.base import get_db
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


# ---------------------------------------------------------------------------
# start / stop — spawn + reap against a fake orchestrator (no docker).
# ---------------------------------------------------------------------------


class _FakeOrchestrator:
    """Records spawn/reap calls; stands in for the real orchestrator singleton."""

    def __init__(self) -> None:
        self.spawned: list[dict[str, Any]] = []
        self.reaped: list[str] = []

    async def spawn_intake_session(
        self,
        session_id: str,
        *,
        project_slug: str | None = None,
        product_id: str | None = None,
        initial_message: str | None = None,
    ) -> object:
        self.spawned.append(
            {
                "session_id": session_id,
                "project_slug": project_slug,
                "product_id": product_id,
                "initial_message": initial_message,
            }
        )
        return SimpleNamespace(agent_id="intake-1")

    async def reap_intake_session(self, session_id: str) -> None:
        self.reaped.append(session_id)


@pytest_asyncio.fixture
async def start_client() -> AsyncIterator[dict[str, Any]]:
    orch = _FakeOrchestrator()
    prev = deps._ServiceHolder.orchestrator
    deps._ServiceHolder.orchestrator = orch  # type: ignore[assignment]

    async def _fake_db() -> AsyncIterator[object]:
        yield object()  # product-scope start never touches it

    app = FastAPI()
    app.include_router(router, prefix="/api/prompter")
    app.dependency_overrides[get_db] = _fake_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield {"client": client, "orch": orch}

    deps._ServiceHolder.orchestrator = prev


@pytest.mark.asyncio
async def test_start_product_scope_spawns_and_returns_session(
    start_client: dict,
) -> None:
    client, orch = start_client["client"], start_client["orch"]
    product_id = str(uuid4())

    resp = await client.post(
        "/api/prompter/live/start",
        json={"product_id": product_id, "initial_message": "build X"},
    )
    assert resp.status_code == HTTPStatus.CREATED
    session_id = resp.json()["session_id"]
    assert session_id
    assert orch.spawned == [
        {
            "session_id": session_id,
            "project_slug": None,
            "product_id": product_id,
            "initial_message": "build X",
        }
    ]


@pytest.mark.asyncio
async def test_start_project_scope_resolves_slug(start_client: dict) -> None:
    client, orch = start_client["client"], start_client["orch"]
    project_id = uuid4()

    fake_svc = SimpleNamespace(
        get=lambda _pid: _async_return(SimpleNamespace(slug="roboco"))
    )
    with patch("roboco.services.project.get_project_service", lambda _db: fake_svc):
        resp = await client.post(
            "/api/prompter/live/start", json={"project_id": str(project_id)}
        )
    assert resp.status_code == HTTPStatus.CREATED
    assert orch.spawned[0]["project_slug"] == "roboco"
    assert orch.spawned[0]["product_id"] is None


@pytest.mark.asyncio
async def test_start_unknown_project_404(start_client: dict) -> None:
    client = start_client["client"]
    fake_svc = SimpleNamespace(get=lambda _pid: _async_return(None))
    with patch("roboco.services.project.get_project_service", lambda _db: fake_svc):
        resp = await client.post(
            "/api/prompter/live/start", json={"project_id": str(uuid4())}
        )
    assert resp.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.asyncio
async def test_start_requires_exactly_one_scope(start_client: dict) -> None:
    client = start_client["client"]
    both = await client.post(
        "/api/prompter/live/start",
        json={"project_id": str(uuid4()), "product_id": str(uuid4())},
    )
    assert both.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    neither = await client.post("/api/prompter/live/start", json={})
    assert neither.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


@pytest.mark.asyncio
async def test_stop_reaps_session(start_client: dict) -> None:
    client, orch = start_client["client"], start_client["orch"]
    resp = await client.post("/api/prompter/live/sess-9/stop")
    assert resp.status_code == HTTPStatus.OK
    assert resp.json() == {"stopped": True}
    assert orch.reaped == ["sess-9"]


def _async_return(value: Any) -> Any:
    """Wrap a value in an awaitable so a lambda can stand in for an async method."""

    async def _coro() -> Any:
        return value

    return _coro()
