"""Token enforcement on the live Secretary chat (Phase 5).

Mirror of ``test_prompter_live_auth`` for the ``secretary_live`` router, which
previously had zero auth on any endpoint. The same ``require_panel_token`` gate
applies at the route level. The Secretary's *authority* (directive execution)
is gated separately at ``/api/secretary/directives``; this only closes the
live-chat transport.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING, Any

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from roboco.agents_config import issue_panel_token
from roboco.api import deps
from roboco.api.routes.secretary_live import router as secretary_live_router
from roboco.services import prompter_live

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

_SECRET = "test-secret-for-secretary-live-auth"
_HTTP_401 = HTTPStatus.UNAUTHORIZED


class _FakeOrchestrator:
    def __init__(self) -> None:
        self.spawned: list[dict[str, Any]] = []
        self.reaped: list[str] = []

    async def start_secretary_session(
        self, session_id: str, *, initial_message: str | None = None
    ) -> None:
        self.spawned.append(
            {"session_id": session_id, "initial_message": initial_message}
        )

    async def reap_secretary_session(self, session_id: str) -> None:
        self.reaped.append(session_id)


@pytest_asyncio.fixture
async def auth_client(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[AsyncClient]:
    orch = _FakeOrchestrator()
    monkeypatch.setattr(deps._ServiceHolder, "orchestrator", orch)

    def container_handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(container_handler))
    registry = prompter_live.PrompterLiveRegistry(http_client=mock_client)
    prompter_live._RegistryHolder.instance = registry

    app = FastAPI()
    app.include_router(secretary_live_router, prefix="/api/secretary")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

    prompter_live._RegistryHolder.instance = None
    await mock_client.aclose()


def _strict(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ROBOCO_AGENT_AUTH_SECRET", _SECRET)
    monkeypatch.setenv("ROBOCO_AGENT_AUTH_REQUIRED", "true")


def _dev(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ROBOCO_AGENT_AUTH_SECRET", _SECRET)
    monkeypatch.delenv("ROBOCO_AGENT_AUTH_REQUIRED", raising=False)


def _start_body() -> dict[str, Any]:
    return {"initial_message": "hi"}


@pytest.mark.asyncio
async def test_start_rejects_missing_token_when_required(
    auth_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _strict(monkeypatch)
    r = await auth_client.post("/api/secretary/live/start", json=_start_body())
    assert r.status_code == _HTTP_401


@pytest.mark.asyncio
async def test_start_rejects_forged_token_when_required(
    auth_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _strict(monkeypatch)
    r = await auth_client.post(
        "/api/secretary/live/start",
        json=_start_body(),
        headers={"X-Agent-Token": "forged-not-a-real-hmac"},
    )
    assert r.status_code == _HTTP_401


@pytest.mark.asyncio
async def test_start_accepts_valid_panel_token(
    auth_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _strict(monkeypatch)
    r = await auth_client.post(
        "/api/secretary/live/start",
        json=_start_body(),
        headers={"X-Agent-Token": issue_panel_token()},
    )
    assert r.status_code == HTTPStatus.CREATED


@pytest.mark.asyncio
async def test_start_rejects_forged_token_even_in_dev(
    auth_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _dev(monkeypatch)
    r = await auth_client.post(
        "/api/secretary/live/start",
        json=_start_body(),
        headers={"X-Agent-Token": "forged-not-a-real-hmac"},
    )
    assert r.status_code == _HTTP_401


@pytest.mark.asyncio
async def test_stream_rejects_missing_token_when_required(
    auth_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _strict(monkeypatch)
    r = await auth_client.get("/api/secretary/live/unknown/stream")
    assert r.status_code == _HTTP_401


@pytest.mark.asyncio
async def test_stream_accepts_valid_panel_token(
    auth_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _strict(monkeypatch)
    r = await auth_client.get(
        "/api/secretary/live/unknown/stream",
        headers={"X-Agent-Token": issue_panel_token()},
    )
    assert r.status_code == HTTPStatus.OK


@pytest.mark.parametrize(
    ("method", "path", "json"),
    [
        ("GET", "/api/secretary/live/unknown/status", None),
        ("POST", "/api/secretary/live/unknown/messages", {"text": "hi"}),
        ("POST", "/api/secretary/live/sess/stop", None),
    ],
)
@pytest.mark.asyncio
async def test_status_send_stop_reject_missing_token_when_required(
    auth_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    path: str,
    json: dict[str, Any] | None,
) -> None:
    _strict(monkeypatch)
    if method == "GET":
        r = await auth_client.get(path)
    else:
        r = await auth_client.post(path, json=json)
    assert r.status_code == _HTTP_401


@pytest.mark.asyncio
async def test_events_ungated_in_strict_mode(
    auth_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``/events`` is the container->relay callback; intentionally ungated
    (scope sentinel mirroring the prompter test)."""
    _strict(monkeypatch)
    r = await auth_client.post(
        "/api/secretary/live/unknown/events", json={"kind": "text"}
    )
    assert r.status_code == HTTPStatus.OK
    assert r.json() == {"pushed": False}
