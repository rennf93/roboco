"""Token enforcement on the live intake chat (Phase 5).

The panel-facing ``prompter_live`` routes used to take no auth dependency — the
SSE stream carried no identity (browser ``EventSource`` can't set headers) and
``start``/``status``/``messages``/``stop`` accepted anonymous calls. The fix
adds a CEO-bound, header-token-only gate (``require_panel_token``) at the route
level: in prod nginx injects the CEO-signed ``X-Agent-Token`` on ``/api/``, and
in dev a missing token is allowed while a presented-but-forged one is still
rejected (matching ``_check_agent_auth_token`` and the WS gate).

These tests mount the router on a bare ``FastAPI()`` (no ``setup_middleware``),
so the gate must raise ``HTTPException(401)`` — the same shape as the a2a auth
tests.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from roboco.agents_config import issue_panel_token
from roboco.api import deps
from roboco.api.routes.prompter_live import router as prompter_live_router
from roboco.db.base import get_db
from roboco.services import prompter_live

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

_SECRET = "test-secret-for-prompter-live-auth"
_HTTP_401 = HTTPStatus.UNAUTHORIZED


class _FakeOrchestrator:
    """Records spawn/reap calls; stands in for the real orchestrator singleton."""

    def __init__(self) -> None:
        self.spawned: list[dict[str, Any]] = []
        self.reaped: list[str] = []

    async def start_intake_session(
        self,
        session_id: str,
        *,
        project_slug: str | None = None,
        product_id: str | None = None,
        project_ids: list[str] | None = None,
        initial_message: str | None = None,
    ) -> None:
        self.spawned.append(
            {
                "session_id": session_id,
                "project_slug": project_slug,
                "product_id": product_id,
                "project_ids": project_ids,
                "initial_message": initial_message,
            }
        )

    async def reap_intake_session(self, session_id: str) -> None:
        self.reaped.append(session_id)


@pytest_asyncio.fixture
async def auth_client(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[AsyncClient]:
    """Mounted router + fake orchestrator + empty registry; no auth env set.

    Each test monkeypatches ``ROBOCO_AGENT_AUTH_SECRET`` and
    ``ROBOCO_AGENT_AUTH_REQUIRED`` to pick dev vs strict mode. The registry is
    empty so the SSE stream over an unknown session yields nothing (200),
    ``status`` reports dead, ``messages`` 404s, and ``/events`` reports
    ``pushed: false`` — all non-401, which is what the "gate passed" assertions
    need.
    """
    orch = _FakeOrchestrator()
    monkeypatch.setattr(deps._ServiceHolder, "orchestrator", orch)

    def container_handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(container_handler))
    registry = prompter_live.PrompterLiveRegistry(http_client=mock_client)
    prompter_live._RegistryHolder.instance = registry

    async def _fake_db() -> AsyncIterator[object]:
        yield object()

    app = FastAPI()
    app.include_router(prompter_live_router, prefix="/api/prompter")
    app.dependency_overrides[get_db] = _fake_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

    prompter_live._RegistryHolder.instance = None
    await mock_client.aclose()
    app.dependency_overrides.clear()


def _strict(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ROBOCO_AGENT_AUTH_SECRET", _SECRET)
    monkeypatch.setenv("ROBOCO_AGENT_AUTH_REQUIRED", "true")


def _dev(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ROBOCO_AGENT_AUTH_SECRET", _SECRET)
    monkeypatch.delenv("ROBOCO_AGENT_AUTH_REQUIRED", raising=False)


def _start_body() -> dict[str, Any]:
    return {"product_id": str(uuid4()), "initial_message": "build X"}


# ---------------------------------------------------------------------------
# /live/start
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_rejects_missing_token_when_required(
    auth_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _strict(monkeypatch)
    r = await auth_client.post("/api/prompter/live/start", json=_start_body())
    assert r.status_code == _HTTP_401


@pytest.mark.asyncio
async def test_start_rejects_forged_token_when_required(
    auth_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _strict(monkeypatch)
    r = await auth_client.post(
        "/api/prompter/live/start",
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
        "/api/prompter/live/start",
        json=_start_body(),
        headers={"X-Agent-Token": issue_panel_token()},
    )
    assert r.status_code == HTTPStatus.CREATED  # gate passed -> 201


@pytest.mark.asyncio
async def test_start_rejects_forged_token_even_in_dev(
    auth_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A presented-but-forged token is rejected even in header-trust mode."""
    _dev(monkeypatch)
    r = await auth_client.post(
        "/api/prompter/live/start",
        json=_start_body(),
        headers={"X-Agent-Token": "forged-not-a-real-hmac"},
    )
    assert r.status_code == _HTTP_401


@pytest.mark.asyncio
async def test_start_dev_mode_missing_token_succeeds(
    auth_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _dev(monkeypatch)
    r = await auth_client.post("/api/prompter/live/start", json=_start_body())
    assert r.status_code == HTTPStatus.CREATED  # dev flow preserved


# ---------------------------------------------------------------------------
# /live/{id}/stream (SSE)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_rejects_missing_token_when_required(
    auth_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _strict(monkeypatch)
    r = await auth_client.get("/api/prompter/live/unknown/stream")
    assert r.status_code == _HTTP_401  # 401 before the EventSourceResponse starts


@pytest.mark.asyncio
async def test_stream_accepts_valid_panel_token(
    auth_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _strict(monkeypatch)
    r = await auth_client.get(
        "/api/prompter/live/unknown/stream",
        headers={"X-Agent-Token": issue_panel_token()},
    )
    assert r.status_code == HTTPStatus.OK  # unknown session -> empty stream -> 200


# ---------------------------------------------------------------------------
# /live/{id}/status, /messages, /stop
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path", "json"),
    [
        ("GET", "/api/prompter/live/unknown/status", None),
        ("POST", "/api/prompter/live/unknown/messages", {"text": "hi"}),
        ("POST", "/api/prompter/live/sess/stop", None),
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


# ---------------------------------------------------------------------------
# /live/{id}/preview-batch — switched from CurrentAgentContext to the panel gate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_preview_batch_passes_with_valid_token_in_strict_mode(
    auth_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _strict(monkeypatch)
    r = await auth_client.post(
        "/api/prompter/live/s1/preview-batch",
        json={"drafts": [{"title": "A"}, {"title": "B"}]},
        headers={"X-Agent-Token": issue_panel_token()},
    )
    # Gate passed -> 200 with waves (preview is pure compute; no session needed).
    assert r.status_code == HTTPStatus.OK


# ---------------------------------------------------------------------------
# /live/{id}/events — container -> relay, intentionally UNGATED (scope sentinel)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_events_ungated_in_strict_mode(
    auth_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``/events`` is the container->relay callback on the internal Docker
    network (opaque session id). Option A leaves it ungated; this test pins
    that decision so a future gating change can't land silently."""
    _strict(monkeypatch)
    r = await auth_client.post(
        "/api/prompter/live/unknown/events", json={"kind": "text"}
    )
    assert r.status_code == HTTPStatus.OK  # ungated -> 200 (pushed: false)
    assert r.json() == {"pushed": False}
